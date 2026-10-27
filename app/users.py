"""Per-user resolution: the `users` table, the current request's user, and the
per-user health.db/vault paths. No sqlite3 import here — every query goes
through app.panel_db.get_db(), same as app.auth and the route modules.

Ids are validated against USER_ID_RE everywhere; a user id or path is never
accepted from request data (see docs/MULTI_USER.md invariant 1).
"""
import os
import re
import time

from flask import current_app, g, has_request_context

from app import auth
from app.panel_db import BOOTSTRAP_USER_ID, get_db
from deploy.bridge_commands import load_profile

USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ROLES = ("admin", "member")
STATUSES = ("active", "disabled")


class InvalidUserId(ValueError):
    pass


def validate_user_id(user_id) -> str:
    if not isinstance(user_id, str) or not USER_ID_RE.fullmatch(user_id):
        raise InvalidUserId(f"invalid user id: {user_id!r}")
    return user_id


def ensure_bootstrap_user(app=None):
    """Idempotently create the 'owner' row pointing at the configured
    HEALTH_DB/VAULT_DIR. Safe to call unconditionally (INSERT OR IGNORE) —
    this is the lazy bootstrap path for a fresh single-user install's first
    `flask enroll-token`."""
    app = app or current_app
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO users(id, display_name, role, status, health_db,"
        " vault_dir, created_at) VALUES(?,?,?,?,?,?,?)",
        (
            BOOTSTRAP_USER_ID,
            app.config.get("HERMES_DISPLAY_NAME") or "OpenHealthAtlas user",
            "admin", "active",
            app.config.get("HEALTH_DB"), app.config.get("VAULT_DIR"),
            int(time.time()),
        ),
    )
    db.commit()


def get_user(user_id):
    if not isinstance(user_id, str):
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def all_users():
    return get_db().execute("SELECT * FROM users ORDER BY created_at").fetchall()


def create_user(user_id, display_name, role="member") -> bool:
    """Insert the panel `users` row. Returns True if a new row was inserted,
    False if `user_id` already existed — idempotent on purpose, so the admin
    panel's create-user flow (broker directory first, this row second) can
    retry safely after a panel-side failure without erroring on the retry."""
    validate_user_id(user_id)
    if role not in ROLES:
        raise ValueError(f"invalid role: {role!r}")
    db = get_db()
    cur = db.execute(
        "INSERT OR IGNORE INTO users(id, display_name, role, status, health_db,"
        " vault_dir, created_at) VALUES(?,?,?,'active',NULL,NULL,?)",
        (user_id, (display_name or user_id).strip()[:80] or user_id, role,
         int(time.time())),
    )
    db.commit()
    return cur.rowcount > 0


def set_role(user_id, role) -> None:
    if role not in ROLES:
        raise ValueError(f"invalid role: {role!r}")
    db = get_db()
    db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    db.commit()


def set_status(user_id, status):
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    db = get_db()
    db.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
    if status == "disabled":
        # Existing sessions must stop working immediately, not just at expiry.
        db.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))
    db.commit()


def active_admin_count() -> int:
    """How many admin accounts are currently active — the admin panel's last-
    admin guard (app/routes/admin.py) refuses a disable/demotion that would
    bring this to zero, so there is always at least one admin left to recover
    the install."""
    return get_db().execute(
        "SELECT COUNT(*) AS n FROM users WHERE role='admin' AND status='active'"
    ).fetchone()["n"]


def users_root(app=None) -> str:
    app = app or current_app
    configured = app.config.get("USERS_ROOT")
    if configured:
        return configured
    return os.path.join(app.config["HERMES_DATA_DIR"], "users")


def user_health_db(user_row, app=None) -> str:
    app = app or current_app
    if is_owner(user_row):
        # The bootstrap owner always tracks the live HEALTH_DB config, exactly
        # as a single-user install behaved before — the stored row value is
        # only a migration artifact (set once, at backfill/bootstrap time).
        return app.config.get("HEALTH_DB") or user_row["health_db"]
    if user_row["health_db"]:
        return user_row["health_db"]
    return os.path.join(users_root(app), user_row["id"], "health.db")


def user_vault_dir(user_row, app=None) -> str:
    app = app or current_app
    if is_owner(user_row):
        return app.config.get("VAULT_DIR") or user_row["vault_dir"]
    if user_row["vault_dir"]:
        return user_row["vault_dir"]
    return os.path.join(users_root(app), user_row["id"], "vault")


def current_user():
    """The requesting user's `users` row, or None (no request, no session,
    unknown user, or a session predating its user_id backfill). Cached on g.
    Bare app-context calls (a unit test, a script) have no request to read a
    session cookie from — they resolve to None, same as being logged out."""
    if "current_user_row" in g:
        return g.current_user_row
    row = None
    if has_request_context():
        session_row = auth.current_session()
        if session_row is not None and session_row["user_id"]:
            row = get_user(session_row["user_id"])
    g.current_user_row = row
    g.user_id = row["id"] if row is not None else None
    return row


def is_owner(user_row) -> bool:
    return user_row is not None and user_row["id"] == BOOTSTRAP_USER_ID


def user_profile_path(user_row, app=None) -> str:
    """Path to a non-owner user's profile.json (docs/MULTI_USER.md "Per-user
    profile settings"). The bootstrap owner has no profile file — it always
    reads the installation env unchanged, same as a single-user install."""
    app = app or current_app
    return os.path.join(users_root(app), user_row["id"], "profile.json")


def current_profile():
    """Read-only effective per-user HERMES_* overlay for display (e.g. the
    dashboard's medication grouping): {} for the owner, or when no user is
    resolved (installation defaults apply) — otherwise the current user's
    own profile.json, validated with the exact same whitelist the broker
    uses (deploy.bridge_commands.load_profile), so the panel can never
    display a key the broker would have refused to honor.

    An absent file reads as {} (no overrides). A PRESENT but invalid file
    also reads as {} here: this is a display-only read with no write to
    refuse, unlike the broker's child-env overlay, which fails the whole
    request closed instead (see deploy/hermes-bridge's _resolve_user_env).
    Cached on g per request."""
    if "current_profile_dict" in g:
        return g.current_profile_dict
    overlay = {}
    user = current_user()
    if user is not None and not is_owner(user):
        loaded, _err = load_profile(user_profile_path(user))
        if loaded is not None:
            overlay = loaded
    g.current_profile_dict = overlay
    return overlay


def medication_settings(app=None):
    """(primary, aliases_csv) medication labels: the current user's own
    profile.json values when they've set them (non-owner only), the
    installation config otherwise — same precedence as current_profile()."""
    app = app or current_app
    profile = current_profile()
    primary = profile.get(
        "HERMES_PRIMARY_MEDICATION",
        app.config.get("HERMES_PRIMARY_MEDICATION", "medication"),
    )
    aliases = profile.get(
        "HERMES_MEDICATION_ALIASES",
        app.config.get("HERMES_MEDICATION_ALIASES", ""),
    )
    return primary, aliases


def current_vault_dir(app=None):
    """The requesting user's vault directory, or the installation default
    when no user is resolved — mirrors app.db_read's health.db fallback."""
    app = app or current_app
    user = current_user()
    if user is not None:
        return user_vault_dir(user, app)
    return app.config.get("VAULT_DIR")
