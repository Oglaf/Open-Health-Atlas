"""Per-user profile.json acceptance tests (docs/MULTI_USER.md "Per-user
profile settings"): a non-owner user's HERMES_* overrides (nutrient targets,
water, medication labels, ...) reach the health.py child through the broker's
env overlay and nothing else, the broker fails closed on anything but a
well-formed whitelisted file, the owner is always untouched, the analysis-job
worker skips (never crashes on) an invalid per-user profile, the panel's
read-only medication grouping reflects the current user's own profile, and
the admin CLI manages profile.json safely (atomic, 0600, whitelisted).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from app import auth as auth_mod
from app import create_app
from app import users as users_mod
from app.panel_db import BOOTSTRAP_USER_ID

SCHEMA = (Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()
BROKER_PATH = Path(__file__).resolve().parent.parent / "deploy" / "hermes-bridge"


def _load_broker():
    loader = importlib.machinery.SourceFileLoader("broker_profile", str(BROKER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    sys.path.insert(0, str(BROKER_PATH.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path[:] = old_path
    return mod


def _write_profile(user_dir: Path, data: dict) -> None:
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "profile.json").write_text(json.dumps(data))


# ------------------------------------------------------------ broker: merge
def test_broker_merges_distinct_profiles_per_user_and_owner_is_unchanged(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    _write_profile(users_root / "alice", {
        "HERMES_PRIMARY_MEDICATION": "alice-med",
        "HERMES_NUTRIENT_TARGETS_JSON": {"vitamin_d": 10},
    })
    _write_profile(users_root / "bob", {
        "HERMES_PRIMARY_MEDICATION": "bob-med",
        "HERMES_NUTRIENT_TARGETS_JSON": {"vitamin_d": 40},
    })
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    monkeypatch.setattr(b, "_audit", lambda _record: None)
    captured = {}

    def fake_run(argv, input=None, env=None, **kwargs):
        from types import SimpleNamespace
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(b.subprocess, "run", fake_run)

    result = b.handle("day-rating", ["green"], 1, None, "alice")
    assert result["ok"] is True
    assert captured["env"]["HERMES_PRIMARY_MEDICATION"] == "alice-med"
    assert json.loads(captured["env"]["HERMES_NUTRIENT_TARGETS_JSON"]) == {"vitamin_d": 10}
    assert captured["env"]["HEALTH_DB"] == str(users_root / "alice" / "health.db")

    captured.clear()
    result = b.handle("day-rating", ["green"], 1, None, "bob")
    assert result["ok"] is True
    assert captured["env"]["HERMES_PRIMARY_MEDICATION"] == "bob-med"
    assert json.loads(captured["env"]["HERMES_NUTRIENT_TARGETS_JSON"]) == {"vitamin_d": 40}

    captured.clear()
    result = b.handle("day-rating", ["green"], 1, None, "owner")
    assert result["ok"] is True
    assert captured["env"] is None   # owner: broker's own env, unchanged by any profile


# -------------------------------------------------------- broker: refusals
def test_broker_refuses_non_whitelisted_profile_key(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    _write_profile(users_root / "eve", {"HERMES_TIMEZONE": "America/New_York"})
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert audited and audited[0]["phase"] == "refused" and audited[0]["user"] == "eve"
    assert "HERMES_TIMEZONE" in audited[0]["error"]


def test_broker_refuses_non_whitelisted_path_key(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    _write_profile(users_root / "eve", {"HEALTH_DB": "/tmp/evil.db"})
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert "HEALTH_DB" in audited[0]["error"]


def test_broker_refuses_symlinked_profile(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    (users_root / "eve").mkdir(parents=True)
    real_target = tmp_path / "outside.json"
    real_target.write_text(json.dumps({"HERMES_PRIMARY_MEDICATION": "x"}))
    (users_root / "eve" / "profile.json").symlink_to(real_target)
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert "symlink" in audited[0]["error"]


def test_broker_refuses_oversized_profile(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    _write_profile(users_root / "eve", {"HERMES_PRIMARY_MEDICATION": "x" * (17 * 1024)})
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert "16 KiB" in audited[0]["error"]


def test_broker_refuses_invalid_json_profile(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    (users_root / "eve").mkdir(parents=True)
    (users_root / "eve" / "profile.json").write_text("{not json")
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert "not valid JSON" in audited[0]["error"]


def test_broker_refuses_non_object_profile(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    _write_profile(users_root / "eve", [1, 2, 3])
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert "JSON object" in audited[0]["error"]


def test_broker_refuses_wrongly_typed_json_key(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    # HERMES_PHASE_OFFSETS_JSON must be an object/array, not a plain number.
    _write_profile(users_root / "eve", {"HERMES_PHASE_OFFSETS_JSON": 5})
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 1, None, "eve")
    assert result["ok"] is False and result["code"] == 2
    assert "HERMES_PHASE_OFFSETS_JSON" in audited[0]["error"]


# ------------------------------------------------------------ worker queue
def test_worker_targets_skip_invalid_profile_and_audit_once(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    (users_root / "good").mkdir(parents=True)
    (users_root / "good" / "health.db").write_text("db")
    (users_root / "bad").mkdir(parents=True)
    (users_root / "bad" / "health.db").write_text("db")
    (users_root / "bad" / "profile.json").write_text("{not json")
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    targets = b._worker_targets()
    health_dbs = {t.get("HEALTH_DB") for t in targets}
    assert str(users_root / "good" / "health.db") in health_dbs
    assert str(users_root / "bad" / "health.db") not in health_dbs
    assert len(audited) == 1
    assert audited[0]["phase"] == "worker-profile-refused" and audited[0]["user"] == "bad"

    b._worker_targets()   # second tick: no duplicate audit for the same user
    assert len(audited) == 1


def test_worker_targets_apply_valid_profile(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    _write_profile(users_root / "carol", {"HERMES_WATER_FALLBACK_ML": 2500})
    (users_root / "carol" / "health.db").write_text("db")
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()

    targets = b._worker_targets()
    carol = next(t for t in targets if t.get("HEALTH_DB") == str(users_root / "carol" / "health.db"))
    assert carol["HERMES_WATER_FALLBACK_ML"] == "2500"


# --------------------------------------------------------- panel: dash.py
def _seed_meds(path: Path, drug: str, dose: float) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO meds_log(date, drug, dose_mg) VALUES(date('now'), ?, ?)",
        (drug, dose),
    )
    con.commit()
    con.close()


@pytest.fixture()
def profile_dash_app(tmp_path):
    data_dir = tmp_path / "hermes-data"
    owner_health = data_dir / "health.db"
    data_dir.mkdir(parents=True)
    # installation default (tests/conftest.py): HERMES_PRIMARY_MEDICATION=medication
    _seed_meds(owner_health, "medication", 5)

    guest_dir = data_dir / "users" / "guest"
    guest_dir.mkdir(parents=True)
    _seed_meds(guest_dir / "health.db", "guest-med", 7)
    _write_profile(guest_dir, {"HERMES_PRIMARY_MEDICATION": "guest-med"})

    broken_dir = data_dir / "users" / "broken"
    broken_dir.mkdir(parents=True)
    _seed_meds(broken_dir / "health.db", "medication", 9)
    (broken_dir / "profile.json").write_text("{not json")

    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(data_dir / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(owner_health),
        "VAULT_DIR": str(data_dir / "vault"),
        "HERMES_DATA_DIR": str(data_dir),
    })
    with app.app_context():
        users_mod.create_user("guest", "Guest", role="member")
        users_mod.create_user("broken", "Broken", role="member")
    return app


def _client_as(app, user_id):
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session(user_id)
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def test_dash_medication_grouping_differs_per_user(profile_dash_app):
    owner = _client_as(profile_dash_app, BOOTSTRAP_USER_ID)
    guest = _client_as(profile_dash_app, "guest")

    owner_safety = owner.get("/api/dash/safety").get_json()
    guest_safety = guest.get("/api/dash/safety").get_json()
    assert owner_safety["medication"] == "medication"
    assert owner_safety["doses"][0]["dose_total_mg"] == 5.0
    assert guest_safety["medication"] == "guest-med"
    assert guest_safety["doses"][0]["dose_total_mg"] == 7.0


def test_dash_medication_falls_back_on_invalid_profile(profile_dash_app):
    """The panel read is display-only (no write to refuse): an invalid
    profile.json falls back to installation config instead of 500ing the
    dashboard — see app.users.current_profile's docstring for why this
    differs from the broker's fail-closed write path."""
    broken = _client_as(profile_dash_app, "broken")
    safety = broken.get("/api/dash/safety").get_json()
    assert safety["medication"] == "medication"
    assert safety["doses"][0]["dose_total_mg"] == 9.0


# -------------------------------------------------------------------- CLI
def test_cli_user_profile_lifecycle(tmp_path):
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir(parents=True)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(data_dir / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HERMES_DATA_DIR": str(data_dir),
    })
    runner = app.test_cli_runner()
    result = runner.invoke(args=["user-create", "bob", "--name", "Bob"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(args=["user-profile", BOOTSTRAP_USER_ID, "--show"])
    assert result.exit_code != 0   # owner refused: it has no profile.json

    result = runner.invoke(args=["user-profile", "ghost", "--show"])
    assert result.exit_code != 0   # unknown user refused

    result = runner.invoke(args=["user-profile", "bob", "--show"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {}   # no profile yet

    result = runner.invoke(args=[
        "user-profile", "bob",
        "--set", "HERMES_PRIMARY_MEDICATION=bob-med",
        "--set", 'HERMES_PHASE_OFFSETS_JSON={"cut":-100,"maintain":0,"bulk":100}',
    ])
    assert result.exit_code == 0, result.output

    profile_path = data_dir / "users" / "bob" / "profile.json"
    assert profile_path.exists()
    assert (profile_path.stat().st_mode & 0o777) == 0o600
    leftover = list((data_dir / "users" / "bob").glob(".profile.*.tmp"))
    assert leftover == []

    result = runner.invoke(args=["user-profile", "bob", "--show"])
    assert result.exit_code == 0
    shown = json.loads(result.output)
    assert shown["HERMES_PRIMARY_MEDICATION"] == "bob-med"
    assert shown["HERMES_PHASE_OFFSETS_JSON"] == {"cut": -100, "maintain": 0, "bulk": 100}

    result = runner.invoke(args=["user-profile", "bob", "--set", "HERMES_TIMEZONE=UTC"])
    assert result.exit_code != 0   # non-whitelisted key refused

    result = runner.invoke(args=[
        "user-profile", "bob", "--set", "HERMES_PHASE_OFFSETS_JSON=not-json",
    ])
    assert result.exit_code != 0   # malformed JSON value for a *_JSON key refused

    result = runner.invoke(args=["user-profile", "bob", "--unset", "HERMES_PRIMARY_MEDICATION"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(args=["user-profile", "bob", "--show"])
    assert result.exit_code == 0
    shown = json.loads(result.output)
    assert "HERMES_PRIMARY_MEDICATION" not in shown
    assert "HERMES_PHASE_OFFSETS_JSON" in shown

    result = runner.invoke(args=["user-profile", "bob", "--clear"])
    assert result.exit_code == 0
    assert not profile_path.exists()
