"""Self-service Profile & targets (fork owner decision 2026-10-29,
docs/PRODUCT_AUTHORITY.md): /api/profile/* lets the logged-in user read and
update their own owner_profile/phase/nutrition_targets/planned_times rows
(the toolkit's existing profile-set/phase-set/nutrition-target-set/
planned-time-set setters, newly allowlisted for the panel) plus their own
profile.json nutrition overlay via the new user-profile-merge broker command.

Two fixtures: `client` stubs app.bridge.run for fast argv/validation pinning
(same pattern as tests/test_log_api.py and tests/test_nutrition_api.py);
`merge_env` drives the REAL broker module over an actual Unix socket (same
pattern as tests/test_admin_panel.py's LiveBroker) specifically for
user-profile-merge, since that is the one new write that touches a real
file and must be pinned end to end, not just as an argv shape."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import socket
import sqlite3
import tempfile
import threading

import pytest

from app import auth as auth_mod
from app import bridge, create_app
from app import users as users_mod
from tests.support import csrf_from

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()
BROKER_PATH = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "hermes-bridge"


def _seed_db(path, *, profile=None, targets=None, planned=None):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for key, value in (profile or {}).items():
        con.execute(
            "INSERT INTO owner_profile(key, value, updated_at) VALUES(?,?,datetime('now'))",
            (key, value),
        )
    for name, target in (targets or {}).items():
        con.execute(
            "INSERT INTO nutrition_targets(name, target, updated) VALUES(?,?,datetime('now'))",
            (name, target),
        )
    for metric, (planned_time, tol) in (planned or {}).items():
        con.execute(
            "INSERT INTO planned_times(metric, planned, tolerance_min, updated)"
            " VALUES(?,?,?,datetime('now'))",
            (metric, planned_time, tol),
        )
    con.commit()
    con.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    _seed_db(
        hdb,
        profile={"height_cm": "180", "sex": "male", "dob": "1990-01-01", "phase": "cut"},
        targets={"kcal": 2200},
        planned={"dose": ("08:00", 30)},
    )
    calls = []

    def fake_run(subcmd, *args, **kw):
        calls.append((subcmd, list(args), kw.get("stdin")))
        if subcmd == "nutrition-targets":
            return {"status": "insufficient_data", "reason": "no weight_kg in body_metrics"}
        return {"ok": True}

    monkeypatch.setattr(bridge, "run", fake_run)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/profile").status_code == 401
    assert app.test_client().post("/api/profile/phase", json={"phase": "cut"}).status_code == 401


def test_get_profile_reads_owner_profile_targets_and_planned_times(client):
    r = client.get("/api/profile")
    assert r.status_code == 200
    d = r.get_json()
    assert d["profile"] == {"height_cm": "180", "sex": "male", "dob": "1990-01-01",
                             "activity_fallback": None}
    assert d["phase"] == "cut"
    assert d["targets"] == {"kcal": 2200}
    assert d["planned_times"] == {"dose": {"planned": "08:00", "tolerance_min": 30}}
    # The default session is the bootstrap owner: no profile.json overlay.
    assert d["per_user_overlay_supported"] is False
    assert "bootstrap owner" in d["per_user_overlay_reason"]
    assert d["engine"]["status"] == "insufficient_data"
    assert "cut" in d["catalog"]["diet_phases"]
    assert "zinc" in d["catalog"]["micro_target_names"]


def test_set_profile_calls_profile_set_per_key(client):
    r = client.post("/api/profile/profile", json={"sex": "female", "height_cm": 165})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert ("profile-set", ["sex", "female"], None) in client.calls
    assert ("profile-set", ["height_cm", "165.0"], None) in client.calls


def test_set_profile_rejects_bad_values_before_bridge(client):
    r = client.post("/api/profile/profile", json={"height_cm": 9000})
    assert r.status_code == 400 and client.calls == []
    r = client.post("/api/profile/profile", json={"sex": "unknown"})
    assert r.status_code == 400 and client.calls == []
    r = client.post("/api/profile/profile", json={})
    assert r.status_code == 400 and client.calls == []


def test_set_phase(client):
    r = client.post("/api/profile/phase", json={"phase": "bulk"})
    assert r.status_code == 200
    assert client.calls[-1] == ("phase-set", ["bulk"], None)
    r = client.post("/api/profile/phase", json={"phase": "nonsense"})
    assert r.status_code == 400


def test_set_target(client):
    r = client.post("/api/profile/targets", json={"name": "kcal", "target": 2100})
    assert r.status_code == 200
    assert client.calls[-1] == ("nutrition-target-set", ["kcal", "--target", "2100.0"], None)
    assert client.post("/api/profile/targets", json={"name": "fat_g", "target": 50}).status_code == 400
    assert client.post("/api/profile/targets", json={"name": "kcal", "target": -5}).status_code == 400


def test_set_planned_time(client):
    r = client.post("/api/profile/planned-time",
                     json={"metric": "wake", "time": "07:30", "tolerance": 20})
    assert r.status_code == 200
    assert client.calls[-1] == (
        "planned-time-set", ["wake", "07:30", "--tolerance", "20"], None,
    )
    # no tolerance -> no flag
    client.calls.clear()
    r = client.post("/api/profile/planned-time", json={"metric": "bed", "time": "23:00"})
    assert r.status_code == 200
    assert client.calls[-1] == ("planned-time-set", ["bed", "23:00"], None)
    assert client.post("/api/profile/planned-time",
                        json={"metric": "nap", "time": "13:00"}).status_code == 400
    assert client.post("/api/profile/planned-time",
                        json={"metric": "wake", "time": "7:30"}).status_code == 400
    assert client.post("/api/profile/planned-time",
                        json={"metric": "wake", "time": "07:30", "tolerance": 1}).status_code == 400


def test_nutrition_config_refused_for_bootstrap_owner(client):
    r = client.post("/api/profile/nutrition-config", json={"nutrient_targets": {"zinc": 10}})
    assert r.status_code == 400
    assert "bootstrap owner" in r.get_json()["error"]
    assert client.calls == []


def test_nutrition_config_value_validation_before_bridge(client, monkeypatch):
    # Pretend this session is a non-owner so the owner-refusal doesn't short
    # circuit before the value validation this test is pinning.
    monkeypatch.setattr(users_mod, "is_owner", lambda row: False)
    r = client.post("/api/profile/nutrition-config",
                     json={"nutrient_targets": {"unobtainium": 5}})
    assert r.status_code == 400 and "unknown nutrient" in r.get_json()["error"]
    assert client.calls == []
    r = client.post("/api/profile/nutrition-config",
                     json={"phase_offsets": {"cut": -500, "maintain": 0}})
    assert r.status_code == 400
    assert client.calls == []
    r = client.post("/api/profile/nutrition-config", json={"unknown_field": {}})
    assert r.status_code == 400
    assert client.calls == []


# --------------------------------------------------------- real-broker merge
def _load_broker(users_root):
    loader = importlib.machinery.SourceFileLoader(
        f"broker_profile_api_{id(users_root)}", str(BROKER_PATH)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    import sys
    old_path = list(sys.path)
    old_env = os.environ.get("BRIDGE_USERS_ROOT")
    sys.path.insert(0, str(BROKER_PATH.parent))
    os.environ["BRIDGE_USERS_ROOT"] = str(users_root)
    try:
        loader.exec_module(mod)
    finally:
        sys.path[:] = old_path
        if old_env is None:
            os.environ.pop("BRIDGE_USERS_ROOT", None)
        else:
            os.environ["BRIDGE_USERS_ROOT"] = old_env
    mod._audit = lambda _record: None
    return mod


def _short_sock():
    fd, path = tempfile.mkstemp(prefix="hb-profile-", suffix=".sock", dir=tempfile.gettempdir())
    os.close(fd)
    os.unlink(path)
    return path


class LiveBroker:
    def __init__(self, path, mod):
        self.mod = mod
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(1 << 20)
                if not data:
                    continue
                try:
                    req = json.loads(data.decode("utf-8"))
                    resp = self.mod.handle(
                        str(req["subcmd"]),
                        [str(a) for a in req.get("args", [])],
                        os.getpid(),
                        (str(req["stdin"]) if req.get("stdin") is not None else None),
                        (str(req["user"]) if req.get("user") is not None else None),
                    )
                except Exception as exc:  # pragma: no cover - defensive only
                    resp = {"ok": False, "code": 1, "stdout": "", "stderr": str(exc)}
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture()
def merge_env(tmp_path):
    data_dir = tmp_path / "hermes-data"
    users_root = data_dir / "users"
    users_root.mkdir(parents=True)
    for user_id in ("alice", "bob"):
        user_dir = users_root / user_id
        user_dir.mkdir()
        _seed_db(user_dir / "health.db")

    mod = _load_broker(users_root)
    sock_path = _short_sock()
    broker = LiveBroker(sock_path, mod)

    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": True, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(data_dir / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HERMES_DATA_DIR": str(data_dir), "USERS_ROOT": str(users_root),
        "BRIDGE_SOCKET": sock_path, "HEALTH_DB": str(users_root / "alice" / "health.db"),
    })
    with app.app_context():
        users_mod.ensure_bootstrap_user()
        users_mod.create_user("alice", "Alice", role="member")
        users_mod.create_user("bob", "Bob", role="member")

    yield app, users_root

    broker.close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


def _client_as(app, user_id):
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session(user_id)
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def _csrf(client):
    return csrf_from(client.get("/profile").get_data(as_text=True))


def test_nutrition_config_merges_into_the_callers_own_profile_json(merge_env):
    app, users_root = merge_env
    alice = _client_as(app, "alice")
    csrf = _csrf(alice)
    r = alice.post("/api/profile/nutrition-config",
                    json={"nutrient_targets": {"zinc": 11, "folate": 400}},
                    headers={"X-CSRFToken": csrf})
    assert r.status_code == 200, r.get_json()
    data = json.loads((users_root / "alice" / "profile.json").read_text())
    assert data == {"HERMES_NUTRIENT_TARGETS_JSON": {"zinc": 11, "folate": 400}}
    assert not (users_root / "bob" / "profile.json").exists()

    # a second call merges rather than replaces, and null removes a key
    r = alice.post("/api/profile/nutrition-config",
                    json={"phase_offsets": {"cut": -500, "maintain": 0, "bulk": 500},
                          "nutrient_targets": None},
                    headers={"X-CSRFToken": csrf})
    assert r.status_code == 200, r.get_json()
    data = json.loads((users_root / "alice" / "profile.json").read_text())
    assert data == {"HERMES_PHASE_OFFSETS_JSON": {"cut": -500, "maintain": 0, "bulk": 500}}


def test_nutrition_config_rejects_bad_merge_values_via_real_broker(merge_env):
    app, users_root = merge_env
    alice = _client_as(app, "alice")
    csrf = _csrf(alice)
    r = alice.post("/api/profile/nutrition-config",
                    json={"protein_per_kg": {"cut": 0, "maintain": 1.6, "bulk": 2.0}},
                    headers={"X-CSRFToken": csrf})
    assert r.status_code == 400
    assert not (users_root / "alice" / "profile.json").exists()


def test_nutrition_config_isolated_per_user(merge_env):
    app, users_root = merge_env
    alice = _client_as(app, "alice")
    bob = _client_as(app, "bob")
    alice.post("/api/profile/nutrition-config", json={"nutrient_targets": {"zinc": 11}},
               headers={"X-CSRFToken": _csrf(alice)})
    bob.post("/api/profile/nutrition-config", json={"nutrient_targets": {"iron": 8}},
             headers={"X-CSRFToken": _csrf(bob)})

    alice_data = json.loads((users_root / "alice" / "profile.json").read_text())
    bob_data = json.loads((users_root / "bob" / "profile.json").read_text())
    assert alice_data == {"HERMES_NUTRIENT_TARGETS_JSON": {"zinc": 11}}
    assert bob_data == {"HERMES_NUTRIENT_TARGETS_JSON": {"iron": 8}}

    # alice's GET never reflects bob's overlay
    r = alice.get("/api/profile")
    assert r.get_json()["nutrition_config"]["nutrient_targets"] == {"zinc": 11}


def test_nutrition_config_requires_csrf_token(merge_env):
    app, _users_root = merge_env
    alice = _client_as(app, "alice")
    r = alice.post("/api/profile/nutrition-config",
                    json={"nutrient_targets": {"zinc": 11}})
    assert r.status_code == 400  # CSRF rejection (Flask-WTF's generic 400)
