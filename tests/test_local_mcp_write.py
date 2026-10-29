"""Real broker-mediated MCP ingest (health_write) contracts.

The MCP server talks to a REAL hermes-bridge broker subprocess over its Unix
socket — the same validated write path the panel uses — and every accepted
write lands in the targeted user's real health.db (visible via health_query).
Fictional data only; the MCP itself never touches SQLite for a write.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import time

import pytest

mcp = pytest.importorskip("mcp", reason="install requirements-mcp.txt for protocol verification")
from mcp.client.stdio import stdio_client
from toolkit.hermes_insights.contracts import canonical_json


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "openhealthatlas_mcp.py"
BROKER = ROOT / "deploy" / "hermes-bridge"

ALICE_RECIPE = "Fictional Berry Oats"
BOB_RECIPE = "Fictional Lentil Vegetable Curry"


def _load_mcp_module():
    return runpy.run_path(str(SERVER), run_name="local_mcp_write_test")


def _load_broker_module():
    loader = importlib.machinery.SourceFileLoader("broker_write_test", str(BROKER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    sys.path.insert(0, str(BROKER.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path[:] = old_path
    return mod


def _short_sock():
    """AF_UNIX paths must be <108 chars; pytest tmp_path can exceed that."""
    fd, path = tempfile.mkstemp(prefix="hb-write-", suffix=".sock",
                                dir=tempfile.gettempdir())
    os.close(fd)
    os.unlink(path)
    return path


def _make_user_db(path: Path, anchor_date: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_demo_db.py"),
         "--output", str(path), "--anchor-date", anchor_date],
        check=True, capture_output=True, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _panel_db(path: Path, rows: dict[str, str]):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    con.executemany("INSERT INTO users(id, status) VALUES (?, ?)", rows.items())
    con.commit()
    con.close()
    return path


@pytest.fixture()
def broker_env(tmp_path):
    """A real broker subprocess (BRIDGE_ALLOWED_UID = this test process's uid,
    full single-value default shape) over a temp users root."""
    users_root = tmp_path / "users"
    users_root.mkdir()
    db_paths = {
        "alice": users_root / "alice" / "health.db",
        "bob": users_root / "bob" / "health.db",
    }
    _make_user_db(db_paths["alice"], "2026-06-30")
    _make_user_db(db_paths["bob"], "2026-05-31")
    sock_path = _short_sock()
    audit = tmp_path / "audit.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(BROKER)],
        env={
            **os.environ,
            "BRIDGE_SOCK": sock_path,
            # A UID LIST on the real broker: this test process's uid plus a
            # never-connected second peer — proves a comma-separated set
            # authorizes the MCP/test process while staying a set.
            "BRIDGE_ALLOWED_UID": f"{os.getuid()},{int(os.getuid()) + 99991}",
            "BRIDGE_AUDIT": str(audit),
            "BRIDGE_USERS_ROOT": str(users_root),
            "BRIDGE_HEALTH": str(ROOT / "toolkit" / "health.py"),
            "BRIDGE_HERMESCTL": str(tmp_path / "hermesctl-never-executed"),
            "BRIDGE_TIMEOUT": "60",
            "HERMES_TIMEZONE": "Europe/Paris",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    ready = False
    deadline = time.monotonic() + 5
    probe = b'{"subcmd":"__readiness__","args":[]}\n'
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"broker exited early with {process.returncode}")
        if not os.path.exists(sock_path):
            time.sleep(0.01)
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(sock_path)
                s.sendall(probe)
                response = s.recv(65536)
            if b"not allowed via the bridge" in response:
                ready = True
                break
        except OSError:
            time.sleep(0.01)
    if not ready:
        process.kill()
        pytest.fail("broker never became ready")
    try:
        yield {"sock": sock_path, "users_root": users_root, "dbs": db_paths,
               "audit": audit, "process": process}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


def _mcp_parameters(tmp_path, users_root, *, bridge_socket=None, panel_db=None,
                   owner_database=None):
    args = [str(SERVER), "--users-root", str(users_root), "--timezone", "UTC"]
    if bridge_socket is not None:
        args += ["--bridge-socket", str(bridge_socket)]
    if owner_database is not None:
        args += ["--owner-database", str(owner_database)]
    if panel_db is not None:
        args += ["--panel-db", str(panel_db)]
    return mcp.StdioServerParameters(
        command=sys.executable,
        args=args,
        cwd=str(tmp_path),
        env={"PYTHONDONTWRITEBYTECODE": "1", "OPENHEALTHATLAS_NATIVE_STATS": "off",
             "HEALTH_DB": str(tmp_path / "unrelated-never-created.db"),
             "HERMES_TIMEZONE": "Europe/Paris"},
    )


async def _call(session, name, arguments):
    response = await session.call_tool(name, arguments)
    body = json.loads(response.content[0].text)
    return body, response.isError


async def _query(session, user, feature_keys, frm, to):
    result, error = await _call(session, "health_query", {
        "user": user, "feature_keys": feature_keys,
        "range": {"from": frm, "to": to}, "view": "summary",
    })
    return result, error


def _write(command, user, command_args=None, positional=None, stdin=None):
    payload = {"user": user, "command": command}
    if positional is not None:
        payload["positional"] = positional
    if command_args is not None:
        payload.update(command_args if "args" in command_args else {"args": command_args})
    if stdin is not None:
        payload["stdin"] = stdin
    return payload


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_health_write_absent_in_single_user_database_mode(tmp_path):
    db = tmp_path / "fictional.db"
    _make_user_db(db, "2026-06-30")
    parameters = mcp.StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER), "--database", str(db), "--timezone", "UTC"],
        cwd=str(tmp_path),
        env={"PYTHONDONTWRITEBYTECODE": "1", "OPENHEALTHATLAS_NATIVE_STATS": "off",
             "HERMES_TIMEZONE": "Europe/Paris"},
    )

    async def exercise():
        async with stdio_client(parameters) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert "health_write" not in names
                result, error = await _call(session, "health_write", {
                    "user": "alice", "command": "day-rating", "positional": ["green"],
                })
                assert error and result["status"] == "refused"

    asyncio.run(exercise())


def test_health_write_declared_but_refuses_without_broker(tmp_path):
    users_root = tmp_path / "users"
    users_root.mkdir()
    _make_user_db(users_root / "alice" / "health.db", "2026-06-30")

    async def exercise():
        async with stdio_client(_mcp_parameters(tmp_path, users_root)) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert "health_write" in names
                result, error = await _call(session, "health_write", {
                    "user": "alice", "command": "day-rating", "positional": ["green"],
                })
                assert error
                assert result == {"status": "refused", "error":
                                  "no broker configured; ingest is unavailable in this deployment"}

    asyncio.run(exercise())


def test_health_write_happy_paths_land_in_each_users_own_database(broker_env, tmp_path):
    users_root = broker_env["users_root"]
    alice_db, bob_db = broker_env["dbs"]["alice"], broker_env["dbs"]["bob"]
    bob_before = _digest(bob_db)
    alice_before = _digest(alice_db)

    async def exercise():
        async with stdio_client(_mcp_parameters(
                tmp_path, users_root, bridge_socket=broker_env["sock"])) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                tool = next(t for t in (await session.list_tools()).tools if t.name == "health_write")
                assert tool is not None

                # bob first: eat + checkin in bob's own db
                eat, error = await _call(session, "health_write", _write(
                    "eat", "bob", {"--portions": 1, "--date": "2026-05-30"},
                    positional=[BOB_RECIPE]))
                assert not error and eat["ok"] is True, json.dumps(eat)
                checkin, error = await _call(session, "health_write", _write(
                    "checkin", "bob", {"--date": "2026-06-20"}, positional=["mood", "4"]))
                assert not error and checkin["ok"] is True, json.dumps(checkin)

                # then alice: day-rating, log-food, log-set in alice's own db
                rating, error = await _call(session, "health_write", _write(
                    "day-rating", "alice", {"--date": "2026-06-29"}, positional=["green"]))
                assert not error and rating["ok"] is True, json.dumps(rating)
                food, error = await _call(session, "health_write", _write(
                    "log-food", "alice", {"--grams": 250, "--date": "2026-06-29"},
                    positional=[ALICE_RECIPE]))
                assert not error and food["ok"] is True, json.dumps(food)
                log_set, error = await _call(session, "health_write", _write(
                    "log-set", "alice",
                    {"--weight": 60, "--reps": 5, "--date": "2026-06-29"},
                    positional=["squat"]))
                assert not error and log_set["ok"] is True, json.dumps(log_set)

                # Both writes visible via health_query afterwards
                bob_rating, error = await _query(
                    session, "bob", ["subjective.day_rating"], "2026-05-01", "2026-06-30")
                assert not error and bob_rating["status"] == "ok"
                # bob's food (eaten on 2026-05-30) shows in his nutrition
                bob_food, error = await _query(
                    session, "bob", ["nutrition.logged.kcal"], "2026-05-01", "2026-06-30")
                assert not error and bob_food["status"] in {"ok", "insufficient_data"}

                # Cross-user isolation: no alice data appears in bob's query and
                # vice versa — the broker child ran against each user's own db.
                alice_rating, error = await _query(
                    session, "alice", ["subjective.day_rating"], "2026-06-01", "2026-06-30")
                assert not error and alice_rating["status"] == "ok"
                alice_json = json.dumps(alice_rating)
                assert "2026-05-30" not in alice_json and "mood" not in alice_json

    asyncio.run(exercise())
    # Each database really changed, byte-for-byte a real SQLite write.
    assert _digest(alice_db) != alice_before
    assert _digest(bob_db) != bob_before
    # The broker audited every accepted write.
    audit_lines = broker_env["audit"].read_text().strip().splitlines()
    assert any((record.get("argv") or [None])[0] == "day-rating"
               and record.get("user") == "alice"
               for record in map(json.loads, audit_lines))


def test_health_write_profile_and_targets_setters_and_user_profile_merge(broker_env, tmp_path):
    """Fork 2026-10-29 (docs/PRODUCT_AUTHORITY.md): profile-set/phase-set/
    nutrition-target-set/planned-time-set and the self-service
    user-profile-merge patch are reachable through health_write per named
    user, with the same per-user isolation every other write command gets."""
    users_root = broker_env["users_root"]
    alice_db, bob_db = broker_env["dbs"]["alice"], broker_env["dbs"]["bob"]

    # make_demo_db.py seeds owner_profile/nutrition_targets/planned_times for
    # BOTH fictional users, so isolation is pinned by snapshotting the field
    # this test does NOT touch for each user and confirming it is unchanged
    # afterward — not by assuming the row is absent.
    import sqlite3

    def _row(db, sql):
        con = sqlite3.connect(db)
        try:
            return con.execute(sql).fetchone()
        finally:
            con.close()

    alice_phase_before = _row(alice_db, "SELECT value FROM owner_profile WHERE key='phase'")
    alice_dose_before = _row(alice_db, "SELECT planned, tolerance_min FROM planned_times WHERE metric='dose'")
    bob_height_before = _row(bob_db, "SELECT value FROM owner_profile WHERE key='height_cm'")
    bob_kcal_before = _row(bob_db, "SELECT target FROM nutrition_targets WHERE name='kcal'")

    async def exercise():
        async with stdio_client(_mcp_parameters(
                tmp_path, users_root, bridge_socket=broker_env["sock"])) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()

                profile_set, error = await _call(session, "health_write", _write(
                    "profile-set", "alice", positional=["height_cm", "180"]))
                assert not error and profile_set["ok"] is True, json.dumps(profile_set)

                phase_set, error = await _call(session, "health_write", _write(
                    "phase-set", "bob", positional=["cut"]))
                assert not error and phase_set["ok"] is True, json.dumps(phase_set)

                target_set, error = await _call(session, "health_write", _write(
                    "nutrition-target-set", "alice", {"--target": 2200}, positional=["kcal"]))
                assert not error and target_set["ok"] is True, json.dumps(target_set)

                planned, error = await _call(session, "health_write", _write(
                    "planned-time-set", "bob", {"--tolerance": 30}, positional=["dose", "08:00"]))
                assert not error and planned["ok"] is True, json.dumps(planned)

                patch = json.dumps({"HERMES_SIT_REACH_NORMAL_CM": 30})
                merge, error = await _call(session, "health_write", _write(
                    "user-profile-merge", "alice", stdin=patch))
                assert not error and merge["ok"] is True, json.dumps(merge)

                # Unknown user refused, same as every other write command.
                ghost, error = await _call(session, "health_write", _write(
                    "user-profile-merge", "ghost", stdin=patch))
                assert error and ghost["status"] == "refused"

    asyncio.run(exercise())

    # Each write landed in the right user's OWN database — and nothing this
    # test didn't ask for moved in the OTHER user's database.
    assert _row(alice_db, "SELECT value FROM owner_profile WHERE key='height_cm'") == ("180",)
    assert _row(alice_db, "SELECT target FROM nutrition_targets WHERE name='kcal'") == (2200.0,)
    assert _row(alice_db, "SELECT value FROM owner_profile WHERE key='phase'") == alice_phase_before
    assert _row(alice_db, "SELECT planned, tolerance_min FROM planned_times WHERE metric='dose'") == alice_dose_before

    assert _row(bob_db, "SELECT value FROM owner_profile WHERE key='phase'") == ("cut",)
    assert _row(bob_db, "SELECT planned, tolerance_min FROM planned_times WHERE metric='dose'") == ("08:00", 30)
    assert _row(bob_db, "SELECT value FROM owner_profile WHERE key='height_cm'") == bob_height_before
    assert _row(bob_db, "SELECT target FROM nutrition_targets WHERE name='kcal'") == bob_kcal_before

    alice_profile = json.loads((users_root / "alice" / "profile.json").read_text())
    assert alice_profile == {"HERMES_SIT_REACH_NORMAL_CM": 30}
    assert not (users_root / "bob" / "profile.json").exists()


_FICTIONAL_BIA_REPORT = {
    "date": "2026-06-29", "tested_at": "10:30", "device": "Relaxmedic",
    "subject_label": "Fictional Agent", "sex": "male", "age_years": 35,
    "height_cm": 175,
    "components": [
        {"component": "weight", "value_kg": 81.2, "ref_low": 57.9, "ref_high": 78.4,
         "pct_of_weight": 100.0, "evaluation": "High"},
        {"component": "body_fat", "value_kg": 21.3, "ref_low": 8.2, "ref_high": 16.3,
         "pct_of_weight": 26.2, "evaluation": "High"},
        {"component": "skeletal_muscle", "value_kg": 35.1, "ref_low": 29.2,
         "ref_high": 37.0, "pct_of_weight": 43.2, "evaluation": "Standard"},
    ],
    "segments": [
        {"kind": "fat", "segment": "trunk", "mass_kg": 12.4, "pct_of_standard": 240.0,
         "evaluation": "High", "std_low_pct": 80, "std_high_pct": 160},
    ],
    "impedance": [
        {"frequency_khz": 20, "segment": "trunk", "ohms": 31.0},
    ],
    "bmi": 26.4, "body_fat_pct": 26.2, "body_score": 71,
    "visceral_fat_grade": 9, "bmr_kcal": 1720, "body_age": 34, "whr": 1.0,
}


def test_health_write_logs_a_body_composition_scan_via_the_broker(broker_env, tmp_path):
    """An agent that read a BIA report (e.g. a photo of the device printout)
    must be able to write the whole scan through health_write in one call —
    docs/PRODUCT_AUTHORITY.md "Fork deployment: body composition analysis
    report"."""
    alice_db = broker_env["dbs"]["alice"]
    before = _digest(alice_db)

    async def exercise():
        async with stdio_client(_mcp_parameters(
                tmp_path, broker_env["users_root"],
                bridge_socket=broker_env["sock"])) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                result, error = await _call(session, "health_write", _write(
                    "log-body-composition", "alice",
                    stdin=json.dumps(_FICTIONAL_BIA_REPORT)))
                assert not error and result["ok"] is True, json.dumps(result)

    asyncio.run(exercise())
    assert _digest(alice_db) != before

    module = _load_mcp_module()
    read_back = module["broker_exchange"](broker_env["sock"], {
        "subcmd": "body-composition", "args": ["--latest"], "user": "alice",
    })
    assert read_back["ok"] is True
    payload = json.loads(read_back["stdout"])
    assert payload["latest"]["date"] == "2026-06-29"
    assert payload["latest"]["bmi"] == 26.4
    weight = next(c for c in payload["latest"]["components"] if c["component"] == "weight")
    assert weight["value_kg"] == 81.2


def test_health_write_respects_the_panel_active_gate_and_unknown_users(broker_env, tmp_path):
    users_root = broker_env["users_root"]
    _make_user_db(tmp_path / "owner-health.db", "2026-06-30")
    panel_db = _panel_db(tmp_path / "panel.db", {
        "alice": "active", "bob": "disabled", "owner": "active",
    })

    async def exercise():
        async with stdio_client(_mcp_parameters(
                tmp_path, users_root, bridge_socket=broker_env["sock"],
                panel_db=panel_db, owner_database=tmp_path / "owner-health.db",
                )) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                # Disabled user refused (panel.db gate)…
                disabled, error = await _call(session, "health_write", _write(
                    "day-rating", "bob", {"--date": "2026-05-30"}, positional=["green"]))
                assert error and disabled["status"] == "refused"
                # …unknown user refused (opaque, like reads)…
                ghost, error = await _call(session, "health_write", _write(
                    "day-rating", "ghost", {"--date": "2026-06-29"}, positional=["green"]))
                assert error and ghost["status"] == "refused"
                # …and a malformed/traversal id is refused exactly like reads.
                traversal, error = await _call(session, "health_write", _write(
                    "day-rating", "../etc", {}, positional=["green"]))
                assert error and traversal["status"] == "refused"

                # Active users keep working.
                ok, error = await _call(session, "health_write", _write(
                    "day-rating", "alice", {"--date": "2026-06-28"}, positional=["yellow"]))
                assert not error and ok["ok"] is True

    asyncio.run(exercise())
    audit_lines = broker_env["audit"].read_text().strip().splitlines()
    phases = [record["phase"] for record in map(json.loads, audit_lines) if "phase" in record]
    assert "refused" in phases and "intent" in phases


def test_hostile_attempts_refused_at_the_mcp_layer_and_independently_by_the_broker(
        broker_env, tmp_path):
    users_root = broker_env["users_root"]
    alice_db = broker_env["dbs"]["alice"]
    before = _digest(alice_db)
    alice_facts_after = ""

    async def exercise():
        nonlocal alice_facts_after
        hostile_write_calls = [
            {"user": "alice", "command": "no-such-command"},
            {"user": "alice", "command": "import-hevy", "positional": ["evil.csv"]},
            {"user": "alice", "command": "user-provision", "positional": ["mallory"]},
            {"user": "alice", "command": "hermes-model-set"},
            {"user": "alice", "command": "day-rating", "positional": ["green"],
             "args": {"--evil": "x"}},
            # stdin only on write-note
            {"user": "alice", "command": "checkin", "positional": ["mood", "4"],
             "stdin": "smuggled note"},
            # oversize stdin (4 KiB cap, write-note only)
            {"user": "alice", "command": "write-note", "positional": ["personal/goals.md"],
             "stdin": "x" * 5000},
            # unknown-user hostile target the resolver refuses
            {"user": "mallory", "command": "day-rating", "positional": ["green"]},
        ]
        async with stdio_client(_mcp_parameters(
                tmp_path, users_root, bridge_socket=broker_env["sock"])) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                for call in hostile_write_calls:
                    result, error = await _call(session, "health_write", call)
                    assert error, json.dumps(result)
                    assert result["status"] == "refused"
                    assert str(users_root) not in json.dumps(result)
                # A genuine write-note still works with a compliant note path.
                note, error = await _call(session, "health_write", _write(
                    "write-note", "alice",
                    positional=["personal/goals.md"], stdin="fictional goals"))
                assert not error and note["ok"] is True, json.dumps(note)
                # Sanity: a read still proves the db is intact and reachable.
                rating, error = await _query(
                    session, "alice", ["subjective.day_rating"], "2026-06-01", "2026-06-30")
                assert not error and rating["status"] in {"ok", "insufficient_data"}
                alice_facts_after = json.dumps(rating)

    asyncio.run(exercise())
    # No hostile call wrote anything (only the single write-note vault write),
    # the database bytes are untouched and the post-write read sees no rating.
    assert _digest(alice_db) == before
    assert "green" not in alice_facts_after
    vault_note = users_root / "alice" / "vault" / "personal" / "goals.md"
    assert vault_note.read_text() == "fictional goals"

    module = _load_mcp_module()
    # ...and the broker refuses the same hostiles independently of the MCP.
    broker_refusals = {
        "import-hevy": {"subcmd": "import-hevy", "args": ["evil.csv"], "user": "alice"},
        "import-recipes": {"subcmd": "import-recipes", "args": ["evil.csv"], "user": "alice"},
        "goal-set": {"subcmd": "goal-set", "args": ["--all"], "user": "alice"},
        "hermes-chat": {"subcmd": "hermes-chat",
                        "args": ["--session", "hermes-panel-x-invalid"], "stdin": "hello",
                        "user": "alice"},
        "bad-flag": {"subcmd": "day-rating", "args": ["green", "--evil", "x"], "user": "alice"},
        "unknown-user": {"subcmd": "day-rating", "args": ["green"], "user": "ghost"},
        "too-many-args": {"subcmd": "day-rating",
                          "args": ["green", *[f"--date-dummy-{i}" for i in range(45)]],
                          "user": "alice"},
    }
    for label, payload in broker_refusals.items():
        response = module["broker_exchange"](broker_env["sock"], payload)
        assert response["ok"] is False and response["code"] == 2, f"{label}: {response}"
        assert response["stderr"], f"{label}: refusal must name a reason"


def test_exposure_subset_pin_and_derived_flag_sets(tmp_path):
    mc = _load_mcp_module()
    write_commands = mc["load_write_commands"]()
    # Pin the exposure subset byte-exactly.
    assert mc["HEALTH_WRITE_COMMANDS"] == (
        "eat", "log-food", "log-set", "routine-set", "routine-remove",
        "routine-undo", "checkin", "day-rating", "write-note", "commitment-set",
        "commitment-list", "log-commitment", "schedule-set", "set-batch", "prep",
        "log-body-composition",
        "profile-set", "phase-set", "nutrition-target-set", "planned-time-set",
        "user-profile-merge",
        "water-add", "supplement-log", "pain-log",
    )
    # Every exposed command exists in the broker's shared declaration and its
    # MCP flag set is exactly the broker's (derived, in sync).
    from deploy.bridge_commands import COMMAND_FLAGS, ADMIN_COMMANDS
    for command, flags in write_commands.items():
        assert command in COMMAND_FLAGS
        assert flags == frozenset(COMMAND_FLAGS[command])
    # The restricted subset never grows into admin or hermesctl territory.
    # (user-profile-merge is deliberately exposed — SELF_SERVICE_COMMANDS,
    # not ADMIN_COMMANDS: it only ever targets the calling user's own
    # profile.json, never a target argument.)
    for forbidden in (*ADMIN_COMMANDS, "hermes-chat", "hermes-model-set",
                      "fitbit-status", "hermes-status", "hermes-spend"):
        assert forbidden not in write_commands


def test_note_stdin_cap_refused_clientside(tmp_path):
    mc = _load_mcp_module()
    write_commands = mc["load_write_commands"]()
    build, error = mc["build_health_write_request"](
        {"user": "alice", "command": "write-note", "positional": ["personal/goals.md"],
         "stdin": "é" * 2100},   # > 4096 bytes once UTF-8 encoded
        write_commands, "alice")
    assert build is None and "4 KiB" in error
    build, error = mc["build_health_write_request"](
        {"user": "alice", "command": "checkin", "positional": ["mood", "4"],
         "stdin": "smuggled"}, write_commands, "alice")
    assert build is None and error == (
        "stdin is only accepted for write-note, log-body-composition, "
        "or user-profile-merge"
    )


def test_health_write_water_add_for_named_user_and_unknown_user_rejected(broker_env, tmp_path):
    """Fork 2026-10-29 (docs/PRODUCT_AUTHORITY.md "manual daily capture"):
    water-add is health_write-reachable, per named user, like every other
    write command — an unknown user is refused opaquely, same as reads."""
    users_root = broker_env["users_root"]
    alice_db = broker_env["dbs"]["alice"]
    before = _digest(alice_db)

    async def exercise():
        async with stdio_client(_mcp_parameters(
                tmp_path, users_root, bridge_socket=broker_env["sock"])) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                ok, error = await _call(session, "health_write", _write(
                    "water-add", "alice", {"--date": "2026-06-29"}, positional=["250"]))
                assert not error and ok["ok"] is True, json.dumps(ok)

                ghost, error = await _call(session, "health_write", _write(
                    "water-add", "ghost", {}, positional=["250"]))
                assert error and ghost["status"] == "refused"

    asyncio.run(exercise())
    assert _digest(alice_db) != before