"""Milestone 1 multi-user acceptance tests (docs/MULTI_USER.md).

Each installation keeps one shared panel.db (sessions/credentials/audit) but
a separate health.db + vault per user. These tests pin the invariants listed
in MULTI_USER.md: no cross-user reads/writes, a missing bridge user is an
error, the broker validates/resolves ids itself, disabled users are rejected
immediately, and a legacy single-user panel.db migrates cleanly to the
bootstrap 'owner' without disturbing its existing credentials/sessions.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import sys
import time

import pytest
from flask import g

from app import auth as auth_mod
from app import bridge, create_app
from app import users as users_mod
from app.panel_db import BOOTSTRAP_USER_ID, SCHEMA_STATEMENTS, get_db
from tests.test_passkey_roundtrip import SoftPasskey

SCHEMA = (Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()
BROKER_PATH = Path(__file__).resolve().parent.parent / "deploy" / "hermes-bridge"
ORIGIN = "http://localhost:5111"


def _seed_health_db(path: Path, marker: int, routine: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO vitals(date, systolic, diastolic) VALUES(date('now'), ?, 80)",
        (marker,),
    )
    con.execute(
        "INSERT INTO routines(routine_name, exercise_title, ex_order, target_sets,"
        " target_reps, target_weight_kg) VALUES(?, 'Bench Press', 1, 3, 8, 60)",
        (routine,),
    )
    con.commit()
    con.close()


@pytest.fixture()
def two_user_app(tmp_path):
    """owner (legacy layout) + guest (users_root/guest), each with distinct
    health.db/vault content, wired exactly as a multi-user install resolves
    them (no files moved for the bootstrap owner)."""
    data_dir = tmp_path / "hermes-data"
    owner_health = data_dir / "health.db"
    owner_vault = data_dir / "vault" / "personal"
    owner_vault.mkdir(parents=True)
    _seed_health_db(owner_health, 100, "Owner Routine")
    (owner_vault / "plan.md").write_text("owner's private plan")

    guest_dir = data_dir / "users" / "guest"
    guest_vault = guest_dir / "vault" / "personal"
    guest_vault.mkdir(parents=True)
    _seed_health_db(guest_dir / "health.db", 200, "Guest Routine")
    (guest_vault / "plan.md").write_text("guest's private plan")

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
    return app


def _client_as(app, user_id):
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session(user_id)
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


# --------------------------------------------------------------- isolation
def test_dashboard_data_route_never_crosses_users(two_user_app):
    owner = _client_as(two_user_app, BOOTSTRAP_USER_ID)
    guest = _client_as(two_user_app, "guest")

    owner_program = owner.get("/api/plans/program").get_json()
    guest_program = guest.get("/api/plans/program").get_json()
    assert owner_program["routine_names"] == ["Owner Routine"]
    assert guest_program["routine_names"] == ["Guest Routine"]


def test_export_table_and_database_never_cross_users(two_user_app):
    owner = _client_as(two_user_app, BOOTSTRAP_USER_ID)
    guest = _client_as(two_user_app, "guest")

    owner_csv = owner.get("/api/export/table?name=vitals&format=csv").get_data(as_text=True)
    guest_csv = guest.get("/api/export/table?name=vitals&format=csv").get_data(as_text=True)
    assert "100" in owner_csv and "200" not in owner_csv
    assert "200" in guest_csv and "100" not in guest_csv

    owner_db = owner.get("/api/export/database").data
    guest_db = guest.get("/api/export/database").data
    assert owner_db != guest_db


def test_vault_backed_note_route_never_crosses_users(two_user_app):
    owner = _client_as(two_user_app, BOOTSTRAP_USER_ID)
    guest = _client_as(two_user_app, "guest")

    owner_note = owner.get("/api/plans/note?path=personal/plan.md").get_json()
    guest_note = guest.get("/api/plans/note?path=personal/plan.md").get_json()
    assert owner_note["content"] == "owner's private plan"
    assert guest_note["content"] == "guest's private plan"


def test_activity_feed_never_shows_another_users_writes(two_user_app):
    """panel.db's audit_log is shared; its feed carries command arguments
    (readings, notes), so /api/log/recent must filter by the session user."""
    with two_user_app.app_context():
        db = get_db()
        for user_id, reading in ((BOOTSTRAP_USER_ID, 111), ("guest", 222)):
            db.execute(
                "INSERT INTO audit_log(ts, event, detail, user_id) VALUES(?,?,?,?)",
                (int(time.time()), "bridge.log",
                 f'{{"args": ["vitals", "systolic={reading}"], "outcome": "exit:0"}}',
                 user_id),
            )
        db.commit()
    owner = _client_as(two_user_app, BOOTSTRAP_USER_ID)
    guest = _client_as(two_user_app, "guest")

    owner_feed = owner.get("/api/log/recent").get_data(as_text=True)
    guest_feed = guest.get("/api/log/recent").get_data(as_text=True)
    assert "systolic=111" in owner_feed and "systolic=222" not in owner_feed
    assert "systolic=222" in guest_feed and "systolic=111" not in guest_feed


def test_disabled_user_session_rejected_immediately(two_user_app):
    guest = _client_as(two_user_app, "guest")
    assert guest.get("/").status_code == 200

    with two_user_app.app_context():
        users_mod.set_status("guest", "disabled")

    r = guest.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    assert guest.get("/api/plans/program").status_code == 401


def test_non_owner_gets_403_on_chat_integrations_and_models(two_user_app):
    guest = _client_as(two_user_app, "guest")
    owner = _client_as(two_user_app, BOOTSTRAP_USER_ID)

    assert guest.get("/api/chat/conversations").status_code == 403
    assert guest.get("/api/integrations/sources").status_code == 403
    assert guest.get("/api/models/status").status_code == 403

    # The owner is unaffected — these routes are gated by role, not broken.
    assert owner.get("/api/chat/conversations").status_code == 200
    assert owner.get("/api/integrations/sources").status_code in (200, 503)
    assert owner.get("/api/models/status").status_code == 200


# ----------------------------------------------------------------- bridge
def test_bridge_run_without_user_in_context_raises(tmp_path):
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
    })
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="no user"):
            bridge.run("day-rating", "green")


def test_bridge_run_carries_the_current_user(tmp_path):
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "BRIDGE_SOCKET": str(tmp_path / "unused.sock"),
    })
    with app.app_context():
        g.user_id = "guest"
        with pytest.raises(bridge.BridgeError, match="unavailable"):
            # No broker is listening; this only proves the payload was built
            # (a missing user would raise *before* any socket connect).
            bridge.run("day-rating", "green")


# ------------------------------------------------------------------ broker
def _load_broker():
    loader = importlib.machinery.SourceFileLoader("broker_multiuser", str(BROKER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    sys.path.insert(0, str(BROKER_PATH.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path[:] = old_path
    return mod


def test_broker_single_user_mode_ignores_the_user_field(monkeypatch):
    monkeypatch.delenv("BRIDGE_USERS_ROOT", raising=False)
    b = _load_broker()
    assert b._resolve_user_env(None) == {}
    assert b._resolve_user_env("owner") == {}
    assert b._resolve_user_env("someone-else") is None


def test_broker_resolves_validates_and_refuses_users(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    users_root.mkdir()
    (users_root / "bob").mkdir()
    (users_root / "bob" / "health.db").write_text("x")
    outside = tmp_path / "outside-the-root"
    outside.mkdir()
    (users_root / "evil").symlink_to(outside)

    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()

    assert b._resolve_user_env("owner") == {}
    assert b._resolve_user_env("bob") == {
        "HEALTH_DB": str(users_root / "bob" / "health.db"),
        "HEALTH_VAULT": str(users_root / "bob" / "vault"),
    }
    assert b._resolve_user_env(None) is None            # missing: never defaulted
    assert b._resolve_user_env("../etc") is None          # traversal: fails the id regex
    assert b._resolve_user_env("bob/x") is None           # "/" fails the id regex
    assert b._resolve_user_env("Bob") is None             # uppercase fails the id regex
    assert b._resolve_user_env("ghost") is None           # well-formed but unknown
    assert b._resolve_user_env("evil") is None            # symlink escaping the root


def test_broker_passes_per_user_env_to_the_child(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    (users_root / "bob").mkdir(parents=True)
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    monkeypatch.setattr(b, "_audit", lambda _record: None)
    captured = {}

    def fake_run(argv, input=None, env=None, **kwargs):
        from types import SimpleNamespace
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(b.subprocess, "run", fake_run)

    result = b.handle("day-rating", ["green"], 123, None, "bob")
    assert result["ok"] is True
    assert captured["env"]["HEALTH_DB"] == str(users_root / "bob" / "health.db")
    assert captured["env"]["HEALTH_VAULT"] == str(users_root / "bob" / "vault")

    captured.clear()
    result = b.handle("day-rating", ["green"], 123, None, "owner")
    assert result["ok"] is True
    assert captured["env"] is None  # owner: this process's own env, unchanged


def test_broker_refuses_unknown_user_and_audits_it(tmp_path, monkeypatch):
    users_root = tmp_path / "users"
    users_root.mkdir()
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(users_root))
    b = _load_broker()
    audited = []
    monkeypatch.setattr(b, "_audit", lambda record: audited.append(record))

    result = b.handle("day-rating", ["green"], 123, None, "ghost")
    assert result["ok"] is False and result["code"] == 2
    assert audited and audited[0]["phase"] == "refused" and audited[0]["user"] == "ghost"


# -------------------------------------------------------------------- CLI
def test_cli_user_lifecycle_and_enroll_token(tmp_path):
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir(parents=True)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(data_dir / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HERMES_DATA_DIR": str(data_dir),
    })
    runner = app.test_cli_runner()

    result = runner.invoke(args=["user-create", "bob", "--name", "Bob", "--role", "member"])
    assert result.exit_code == 0, result.output
    assert (data_dir / "users" / "bob" / "health.db").exists()
    assert (data_dir / "users" / "bob" / "vault").is_dir()

    result = runner.invoke(args=["user-create", "bob"])
    assert result.exit_code != 0  # already exists

    result = runner.invoke(args=["user-list"])
    assert result.exit_code == 0
    assert "bob" in result.output and "member" in result.output

    result = runner.invoke(args=["user-disable", "bob"])
    assert result.exit_code == 0
    with app.app_context():
        assert users_mod.get_user("bob")["status"] == "disabled"

    result = runner.invoke(args=["enroll-token", "--user", "bob"])
    assert result.exit_code != 0  # disabled: enrollment refused

    result = runner.invoke(args=["user-enable", "bob"])
    assert result.exit_code == 0
    with app.app_context():
        assert users_mod.get_user("bob")["status"] == "active"

    result = runner.invoke(args=["enroll-token", "--user", "bob"])
    assert result.exit_code == 0
    assert "/enroll?token=" in result.output

    result = runner.invoke(args=["enroll-token", "--user", "ghost"])
    assert result.exit_code != 0

    # Default (no --user): the bootstrap owner, lazily created on first use —
    # preserves today's single-user `flask enroll-token` behavior exactly.
    with app.app_context():
        assert users_mod.get_user(BOOTSTRAP_USER_ID) is None
    result = runner.invoke(args=["enroll-token"])
    assert result.exit_code == 0
    with app.app_context():
        owner = users_mod.get_user(BOOTSTRAP_USER_ID)
        assert owner is not None and owner["role"] == "admin"


# -------------------------------------------------------------- migration
def test_legacy_panel_db_migrates_to_bootstrap_owner(tmp_path):
    panel_db_path = tmp_path / "legacy-panel.db"
    health_db_path = tmp_path / "health.db"
    vault_dir = tmp_path / "vault"
    _seed_health_db(health_db_path, 1, "Legacy Routine")
    vault_dir.mkdir()

    con = sqlite3.connect(panel_db_path)
    # The exact pre-migration schema: every USER_SCOPED_TABLES statement,
    # minus the `users` table itself, which old installs never had.
    for statement in SCHEMA_STATEMENTS[1:]:
        con.execute(statement)
    credential_id = b"legacy-credential-bytes"
    now = int(time.time())
    con.execute(
        "INSERT INTO credentials(credential_id, public_key, sign_count, transports,"
        " label, created_at) VALUES(?,?,?,?,?,?)",
        (credential_id, b"legacy-public-key", 3, "internal", "legacy device", now),
    )
    token = "legacy-session-token"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    con.execute(
        "INSERT INTO sessions(token_hash, created_at, expires_at, last_seen_at)"
        " VALUES(?,?,?,?)",
        (token_hash, now, now + 3600, now),
    )
    con.execute("INSERT INTO audit_log(ts, event, detail) VALUES(?,?,?)",
               (now, "auth.login", "legacy device"))
    con.commit()
    con.close()

    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(panel_db_path), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(health_db_path), "VAULT_DIR": str(vault_dir),
    })
    with app.app_context():
        db = get_db()
        owners = db.execute("SELECT * FROM users").fetchall()
        assert len(owners) == 1
        owner = owners[0]
        assert owner["id"] == BOOTSTRAP_USER_ID
        assert owner["role"] == "admin" and owner["status"] == "active"
        assert owner["health_db"] == str(health_db_path)
        assert owner["vault_dir"] == str(vault_dir)

        cred = db.execute(
            "SELECT user_id, sign_count FROM credentials WHERE credential_id=?",
            (credential_id,),
        ).fetchone()
        assert cred["user_id"] == BOOTSTRAP_USER_ID and cred["sign_count"] == 3

        sess = db.execute(
            "SELECT user_id FROM sessions WHERE token_hash=?", (token_hash,)
        ).fetchone()
        assert sess["user_id"] == BOOTSTRAP_USER_ID

        row = db.execute(
            "SELECT user_id FROM audit_log WHERE event='auth.login'"
        ).fetchone()
        assert row["user_id"] == BOOTSTRAP_USER_ID

    # The existing session still authenticates post-migration, unchanged.
    client = app.test_client()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    assert client.get("/").status_code == 200
    assert client.get("/api/plans/program").get_json()["routine_names"] == ["Legacy Routine"]


# ------------------------------------------------------------- passkeys
def test_passkey_login_and_registration_are_scoped_per_user(tmp_path):
    """A real WebAuthn-style roundtrip (modelled on test_passkey_roundtrip.py)
    for two users: enrollment binds the credential to the right user, a
    typed username restricts login to that user's credential, and a bare
    discoverable login still resolves to the owner."""
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir(parents=True)
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(data_dir / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "HERMES_DATA_DIR": str(data_dir),
        "PANEL_ORIGIN": ORIGIN,
    })
    with app.app_context():
        users_mod.create_user("bob", "Bob", role="member")
        owner_token = auth_mod.new_enroll_token()
        bob_token = auth_mod.new_enroll_token("bob")

    owner_device = SoftPasskey()
    bob_device = SoftPasskey()
    owner_client = app.test_client()
    bob_client = app.test_client()

    def _enroll(client, token, device):
        r = client.post("/api/auth/register/options", json={"token": token})
        assert r.status_code == 200
        opts = r.get_json()
        payload = device.register(r.get_data(as_text=True), origin=ORIGIN)
        r = client.post(
            "/api/auth/register/verify",
            json={"token": token, "credential": payload, "label": "test device"},
        )
        assert r.status_code == 200 and r.get_json()["ok"] is True
        return opts

    owner_opts = _enroll(owner_client, owner_token, owner_device)
    bob_opts = _enroll(bob_client, bob_token, bob_device)
    assert owner_opts["authenticatorSelection"]["residentKey"] == "preferred"
    assert bob_opts["authenticatorSelection"]["residentKey"] == "required"

    assert owner_client.get("/api/models/status").status_code == 200
    assert bob_client.get("/api/models/status").status_code == 403

    owner_client.post("/logout")
    bob_client.post("/logout")
    assert owner_client.get("/").status_code == 302

    # A typed username restricts allow_credentials to that user alone.
    r = bob_client.post("/api/auth/login/options", json={"username": "bob"})
    assert r.status_code == 200
    assertion = bob_device.authenticate(r.get_data(as_text=True), origin=ORIGIN)
    r = bob_client.post("/api/auth/login/verify", json={"credential": assertion})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert bob_client.get("/api/models/status").status_code == 403

    # An unknown username gets the same shape as a real one (no existence leak).
    r = bob_client.post("/api/auth/login/options", json={"username": "nobody-here"})
    assert r.status_code == 200 and "challenge" in r.get_json()

    # No username: discoverable login still finds the owner's passkey.
    r = owner_client.post("/api/auth/login/options", json={})
    assert r.status_code == 200
    assertion = owner_device.authenticate(r.get_data(as_text=True), origin=ORIGIN)
    r = owner_client.post("/api/auth/login/verify", json={"credential": assertion})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert owner_client.get("/api/models/status").status_code == 200
