"""Admin panel acceptance tests (docs/MULTI_USER.md "Admin UI"): Christian
(admin) manages family members from the web UI alone — no CLI, no assistant.

The create-user and profile-settings flows are exercised through the REAL
broker module (deploy/hermes-bridge), loaded in-process and driven over an
actual Unix socket exactly like app.bridge.run() drives the production
broker — not a canned stub — so the panel<->broker contract for the three new
admin commands (user-provision, user-profile-write, user-profile-clear) is
pinned end to end, the same way tests/test_multi_user.py pins the existing
per-user resolution.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from app import auth as auth_mod
from app import create_app
from app import users as users_mod
from app.panel_db import BOOTSTRAP_USER_ID, get_db
from tests.support import csrf_from

BROKER_PATH = Path(__file__).resolve().parent.parent / "deploy" / "hermes-bridge"
ORIGIN = "http://localhost:5111"


# ------------------------------------------------------------- real broker
def _load_broker(users_root):
    """A fresh copy of the real broker module, wired to `users_root` exactly
    like deploy/install-bridge.sh or deploy/docker/oha would via
    BRIDGE_USERS_ROOT — not monkeypatched behavior, the real module-level
    config variable the shipped broker reads."""
    loader = importlib.machinery.SourceFileLoader(
        f"broker_admin_panel_{id(users_root)}", str(BROKER_PATH)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    old_env = os.environ.get("BRIDGE_USERS_ROOT")
    sys.path.insert(0, str(BROKER_PATH.parent))
    os.environ["BRIDGE_USERS_ROOT"] = str(users_root) if users_root else ""
    try:
        loader.exec_module(mod)
    finally:
        sys.path[:] = old_path
        if old_env is None:
            os.environ.pop("BRIDGE_USERS_ROOT", None)
        else:
            os.environ["BRIDGE_USERS_ROOT"] = old_env
    if not users_root:
        mod.BRIDGE_USERS_ROOT = None
    mod._audit = lambda _record: None  # no root-owned audit log in a test sandbox
    return mod


def _short_sock():
    socket_root = os.environ.get("HERMES_TEST_SOCKET_DIR")
    if socket_root is None and os.path.isdir("/private/tmp"):
        socket_root = "/private/tmp"
    fd, path = tempfile.mkstemp(prefix="hb-admin-", suffix=".sock",
                                 dir=socket_root or tempfile.gettempdir())
    os.close(fd)
    os.unlink(path)
    return path


class LiveBroker:
    """Forwards each connection straight into the real broker's handle() —
    the exact dispatch/validation/execution code path deploy/hermes-bridge's
    own socketserver.Handler uses, minus the SO_PEERCRED check (irrelevant
    here: this is testing the subcommand contract, not the transport auth,
    which test_bridge.py and test_multi_user.py already pin separately)."""

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
def admin_env(tmp_path):
    data_dir = tmp_path / "hermes-data"
    users_root = data_dir / "users"
    users_root.mkdir(parents=True)

    mod = _load_broker(users_root)
    sock_path = _short_sock()
    broker = LiveBroker(sock_path, mod)

    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(data_dir / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "HERMES_DATA_DIR": str(data_dir),
        "USERS_ROOT": str(users_root),
        "BRIDGE_SOCKET": sock_path,
        "PANEL_ORIGIN": ORIGIN,
    })
    with app.app_context():
        users_mod.ensure_bootstrap_user()

    yield app, broker, users_root

    broker.close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


def _client_as(app, user_id):
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session(user_id)
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def _create_via_api(admin_client, user_id, display_name, role="member"):
    r = admin_client.post("/api/admin/users",
                           json={"id": user_id, "display_name": display_name, "role": role})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["user"]


def _audit_rows(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute(
            "SELECT ts, event, detail, user_id FROM audit_log ORDER BY id")]


# -------------------------------------------------------------- access control
def test_access_control_and_nav_entry(admin_env):
    app, _broker, _users_root = admin_env
    with app.app_context():
        users_mod.create_user("mallory", "Mallory", role="member")

    anon = app.test_client()
    r = anon.get("/admin")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert anon.get("/api/admin/users").status_code == 401

    member = _client_as(app, "mallory")
    assert member.get("/admin").status_code == 403
    assert member.get("/api/admin/users").status_code == 403
    assert member.post("/api/admin/users", json={}).status_code == 403
    # Members never see the Admin nav entry.
    assert b'href="/admin"' not in member.get("/").data

    admin = _client_as(app, BOOTSTRAP_USER_ID)
    assert admin.get("/admin").status_code == 200
    assert admin.get("/api/admin/users").status_code == 200
    assert b'href="/admin"' in admin.get("/").data

    # "Disabled admin" access control: a disabled account's existing session
    # must stop authenticating immediately, admin role notwithstanding.
    with app.app_context():
        users_mod.create_user("coadmin", "Co-admin", role="admin")
    coadmin = _client_as(app, "coadmin")
    assert coadmin.get("/admin").status_code == 200
    with app.app_context():
        users_mod.set_status("coadmin", "disabled")
    r = coadmin.get("/admin")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert coadmin.get("/api/admin/users").status_code == 401


def test_csrf_required_on_every_admin_post(tmp_path):
    users_root = tmp_path / "users"
    users_root.mkdir()
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "HERMES_DATA_DIR": str(tmp_path / "hermes-data"),
        "USERS_ROOT": str(users_root),
        "BRIDGE_SOCKET": str(tmp_path / "unused.sock"),
        "PANEL_ORIGIN": ORIGIN,
    })
    with app.app_context():
        users_mod.ensure_bootstrap_user()
        users_mod.create_user("alex", "Alex", role="member")
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session(BOOTSTRAP_USER_ID)
    client.set_cookie(auth_mod.SESSION_COOKIE, token)

    posts = [
        ("/api/admin/users", {"id": "zed", "role": "member"}),
        ("/api/admin/users/alex/enroll-link", {}),
        ("/api/admin/users/alex/status", {"status": "disabled"}),
        ("/api/admin/users/alex/role", {"role": "admin"}),
        ("/api/admin/users/alex/profile", {"profile": {}}),
        ("/api/admin/users/alex/profile/reset", {}),
    ]
    for url, body in posts:
        r = client.post(url, json=body)
        assert r.status_code == 400, url
        assert "CSRF" in r.get_json()["error"]

    # A valid token for the same session is accepted (proves the 400s above
    # were really about the missing token, not some other failure).
    html = client.get("/admin").get_data(as_text=True)
    token_value = csrf_from(html)
    r = client.post("/api/admin/users/alex/status", json={"status": "disabled"},
                     headers={"X-CSRFToken": token_value})
    assert r.status_code == 200


# -------------------------------------------------------------------- create
def test_create_user_happy_path_and_refusals(admin_env):
    app, broker, users_root = admin_env
    admin = _client_as(app, BOOTSTRAP_USER_ID)

    r = admin.post("/api/admin/users", json={"id": "alice", "display_name": "Alice", "role": "member"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["user"]["id"] == "alice" and body["user"]["role"] == "member"
    assert (users_root / "alice" / "health.db").is_file()
    assert (users_root / "alice" / "vault").is_dir()
    assert oct(os.stat(users_root / "alice").st_mode)[-3:] == "750"
    with app.app_context():
        row = users_mod.get_user("alice")
        assert row is not None and row["status"] == "active"

    # Idempotent retry: the broker directory already exists, the panel row
    # already exists — a second identical create must not fail.
    r2 = admin.post("/api/admin/users", json={"id": "alice", "display_name": "Alice", "role": "member"})
    assert r2.status_code == 409  # the panel row already exists — a normal duplicate, not a crash

    # Simulate "panel insert failed after a successful broker create": drop
    # just the panel row and retry — the broker side is already done
    # (idempotent), so the retry must succeed cleanly.
    with app.app_context():
        get_db().execute("DELETE FROM users WHERE id='alice'")
        get_db().commit()
    r3 = admin.post("/api/admin/users", json={"id": "alice", "display_name": "Alice Again", "role": "member"})
    assert r3.status_code == 201, r3.get_json()
    assert (users_root / "alice" / "health.db").is_file()  # never recreated/overwritten

    # Duplicate id (now really present again).
    r4 = admin.post("/api/admin/users", json={"id": "alice", "role": "member"})
    assert r4.status_code == 409

    # Reserved / invalid / traversal ids never reach the broker.
    for bad_id in ("owner", "Alice", "../etc", "a/b", "-nope", "", "a" * 33):
        r = admin.post("/api/admin/users", json={"id": bad_id, "role": "member"})
        assert r.status_code == 400, bad_id
        if bad_id:
            assert not (users_root / bad_id).exists()

    # Invalid role.
    r = admin.post("/api/admin/users", json={"id": "zed", "role": "superuser"})
    assert r.status_code == 400

    # Single-user mode (BRIDGE_USERS_ROOT unset on the broker): a clear
    # refusal, not a crash, and no directory is left behind.
    broker.mod.BRIDGE_USERS_ROOT = None
    try:
        r = admin.post("/api/admin/users", json={"id": "newperson", "role": "member"})
        assert r.status_code == 400
        assert "multi-user" in r.get_json()["error"]
        assert not (users_root / "newperson").exists()
    finally:
        broker.mod.BRIDGE_USERS_ROOT = str(users_root)

    audits = _audit_rows(app)
    creates = [a for a in audits if a["event"] == "admin.user_create"]
    assert creates and creates[0]["user_id"] == BOOTSTRAP_USER_ID and creates[0]["detail"] == "alice"


# --------------------------------------------------------------- enrollment
def test_enrollment_link_lifecycle(admin_env):
    app, _broker, _users_root = admin_env
    admin = _client_as(app, BOOTSTRAP_USER_ID)
    with app.app_context():
        users_mod.create_user("bob", "Bob", role="member")

    r = admin.post("/api/admin/users/bob/enroll-link", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["url"].startswith(f"{ORIGIN}/enroll?token=")
    assert body["expires_in_seconds"] == auth_mod.ENROLL_TTL_SECONDS
    token = body["url"].split("token=", 1)[1]

    # Shown once: the server never stores or re-serves the raw token anywhere
    # a GET could retrieve it again (only its hash lives in enroll_tokens).
    with app.app_context():
        stored = get_db().execute("SELECT token_hash FROM enroll_tokens").fetchall()
    assert all(row["token_hash"] != token for row in stored)

    # It really works with the existing enrollment flow.
    anon = app.test_client()
    page = anon.get(f"/enroll?token={token}").get_data(as_text=True)
    assert "Bob" in page

    # Expires: backdate it directly (same effect as waiting 15 minutes).
    with app.app_context():
        get_db().execute("UPDATE enroll_tokens SET expires_at=0 WHERE user_id='bob'")
        get_db().commit()
        assert auth_mod.enroll_token_valid(token) is False
    r = anon.post("/api/auth/register/options", json={"token": token})
    assert r.status_code == 403

    # Disabled users cannot get a link.
    with app.app_context():
        users_mod.set_status("bob", "disabled")
    r = admin.post("/api/admin/users/bob/enroll-link", json={})
    assert r.status_code == 400

    # Audit records the issuance and target, never the token.
    audits = _audit_rows(app)
    issued = [a for a in audits if a["event"] == "admin.enroll_link_issued"]
    assert issued and issued[0]["detail"] == "bob" and issued[0]["user_id"] == BOOTSTRAP_USER_ID
    assert all(token not in json.dumps(a) for a in audits)


# ------------------------------------------------------------- disable/role
def test_disable_guards_and_session_revocation(admin_env):
    app, _broker, _users_root = admin_env
    with app.app_context():
        users_mod.create_user("carl", "Carl", role="member")

    admin = _client_as(app, BOOTSTRAP_USER_ID)
    carl = _client_as(app, "carl")
    assert carl.get("/").status_code == 200

    # Disabling revokes sessions immediately and blocks future login.
    r = admin.post("/api/admin/users/carl/status", json={"status": "disabled"})
    assert r.status_code == 200
    assert carl.get("/").status_code == 302
    assert carl.get("/api/plans/program").status_code == 401

    # Owner can never be disabled (also covers self-disable, since the only
    # admin at this point is the owner acting on themselves).
    r = admin.post(f"/api/admin/users/{BOOTSTRAP_USER_ID}/status", json={"status": "disabled"})
    assert r.status_code == 400


def test_role_change_guards_and_takes_effect_next_request(admin_env):
    app, _broker, _users_root = admin_env
    with app.app_context():
        users_mod.create_user("dana", "Dana", role="member")

    admin = _client_as(app, BOOTSTRAP_USER_ID)

    # Owner's role is fixed (also covers self-demotion for the owner).
    r = admin.post(f"/api/admin/users/{BOOTSTRAP_USER_ID}/role", json={"role": "member"})
    assert r.status_code == 400

    # Promoting dana is fine (owner remains a second active admin either way).
    r = admin.post("/api/admin/users/dana/role", json={"role": "admin"})
    assert r.status_code == 200

    dana_client = _client_as(app, "dana")
    # Role change takes effect on dana's NEXT request via the `users` row —
    # not retroactively, and not carried by the session cookie itself. The
    # admin API's own role gate is the probe (chat/integrations/models are
    # gated by is_owner, not role=='admin' — a separate Phase 1 scope rule).
    assert dana_client.get("/api/admin/users").status_code == 200

    r = admin.post("/api/admin/users/dana/role", json={"role": "member"})
    assert r.status_code == 200
    # The very next request already reflects the demotion.
    assert dana_client.get("/api/admin/users").status_code == 403


def test_last_active_admin_cannot_disable_or_demote_self(admin_env):
    """Owner is permanently active+admin (tested separately above), so under
    normal operation it always backstops the admin pool and the self-/owner-
    guards alone explain every reachable refusal. To exercise the "last
    active admin" predicate (app.users.active_admin_count()) on its own
    terms, this bypasses the route layer (not the HTTP API) to take owner
    out of the active pool directly, leaving a single non-owner admin."""
    app, _broker, _users_root = admin_env
    with app.app_context():
        users_mod.create_user("sole", "Sole Admin", role="admin")
        users_mod.set_status(BOOTSTRAP_USER_ID, "disabled")
        assert users_mod.active_admin_count() == 1

    sole = _client_as(app, "sole")
    assert sole.get("/admin").status_code == 200

    r = sole.post("/api/admin/users/sole/status", json={"status": "disabled"})
    assert r.status_code == 400
    r = sole.post("/api/admin/users/sole/role", json={"role": "member"})
    assert r.status_code == 400


# ------------------------------------------------------------------ profile
def test_profile_roundtrip_through_broker_and_scoping(admin_env):
    app, _broker, users_root = admin_env
    admin = _client_as(app, BOOTSTRAP_USER_ID)
    # Created through the admin API (not users_mod.create_user directly) so
    # the broker has actually provisioned their directory — profile writes
    # for a user the broker has never heard of are correctly refused.
    _create_via_api(admin, "erin", "Erin")
    _create_via_api(admin, "frank", "Frank")

    # Nothing set yet.
    r = admin.get("/api/admin/users/erin/profile")
    assert r.status_code == 200 and r.get_json()["profile"] == {}

    # Valid write.
    profile = {"HERMES_PRIMARY_MEDICATION": "atorvastatin", "HERMES_WATER_ML_PER_KG": 32}
    r = admin.post("/api/admin/users/erin/profile", json={"profile": profile})
    assert r.status_code == 200
    stored = json.loads((users_root / "erin" / "profile.json").read_text())
    assert stored == profile
    assert oct(os.stat(users_root / "erin" / "profile.json").st_mode)[-3:] == "600"

    # Effective only for erin, not frank.
    r = admin.get("/api/admin/users/frank/profile")
    assert r.get_json()["profile"] == {}
    r = admin.get("/api/admin/users/erin/profile")
    assert r.get_json()["profile"] == profile

    # Invalid key refused, file unchanged.
    r = admin.post("/api/admin/users/erin/profile", json={"profile": {"NOT_A_KEY": 1}})
    assert r.status_code == 400
    assert json.loads((users_root / "erin" / "profile.json").read_text()) == profile

    # Malformed body (not even a dict under "profile").
    r = admin.post("/api/admin/users/erin/profile", json={"profile": "nope"})
    assert r.status_code == 400

    # Oversize.
    huge = {"HERMES_MEDICATION_ALIASES": "x" * 20000}
    r = admin.post("/api/admin/users/erin/profile", json={"profile": huge})
    assert r.status_code == 400

    # The owner has no per-user profile.
    r = admin.get(f"/api/admin/users/{BOOTSTRAP_USER_ID}/profile")
    assert r.status_code == 400
    r = admin.post(f"/api/admin/users/{BOOTSTRAP_USER_ID}/profile", json={"profile": {}})
    assert r.status_code == 400

    # Reset removes the file.
    r = admin.post("/api/admin/users/erin/profile/reset", json={})
    assert r.status_code == 200
    assert not (users_root / "erin" / "profile.json").exists()
    r = admin.get("/api/admin/users/erin/profile")
    assert r.get_json()["profile"] == {}

    # Audit carries key names, never values.
    audits = _audit_rows(app)
    updates = [a for a in audits if a["event"] == "admin.profile_update"]
    assert updates
    assert "atorvastatin" not in json.dumps(updates)
    assert "HERMES_PRIMARY_MEDICATION" in updates[0]["detail"]


# --------------------------------------------------------------------- xss
def test_template_escapes_hostile_display_name(admin_env):
    app, _broker, _users_root = admin_env
    hostile = "<script>alert(1)</script>"
    with app.app_context():
        users_mod.create_user("mallory", hostile, role="member")
    admin = _client_as(app, BOOTSTRAP_USER_ID)

    body = admin.get("/api/admin/users").get_json()
    assert any(u["display_name"] == hostile for u in body["users"])

    # The admin page itself never server-renders user-supplied display names
    # (the table is built client-side from JSON via textContent) — the raw
    # page HTML must not contain the unescaped payload either way.
    page = admin.get("/admin").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page
