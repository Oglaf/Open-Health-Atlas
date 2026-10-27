"""Admin panel API (docs/MULTI_USER.md "Admin UI"): lets an admin add and
manage family members (list, create, enrollment links, enable/disable, role
change, per-user profile settings) entirely from the web UI — no CLI or
assistant required.

Every mutation that touches a user's OWN directory (health.db, vault,
profile.json) crosses the broker via app.bridge.run(), exactly like every
other panel write (see docs/ARCHITECTURE.md "write bridge"); this module
never opens or writes a user's directory itself — the panel container mounts
the data volume read-only and can write only panel.db. Only the panel's own
`users` table (panel.db) is written directly here, same as the existing
`flask user-create`/`user-disable` CLI (app/__init__.py).
"""
import json
import os

from flask import Blueprint, current_app, jsonify, request

from app import auth, bridge, users
from app.panel_db import BOOTSTRAP_USER_ID
from deploy.bridge_commands import (
    PROFILE_JSON_KEYS, PROFILE_KEYS, PROFILE_MAX_BYTES, ProfileError,
    read_profile_file, validate_profile,
)

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.before_request
def _admin_only():
    # app.require_login already guarantees an active session by this point
    # (anonymous -> redirect/401 before any blueprint runs); this adds the
    # role check (member -> 403).
    user = users.current_user()
    if user is None or user["role"] != "admin":
        return jsonify(error="admin access required"), 403


def _has_profile(row) -> bool:
    if users.is_owner(row):
        return False
    try:
        return os.path.isfile(users.user_profile_path(row))
    except OSError:
        return False


def _serialize_user(row, actor=None):
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "role": row["role"],
        "status": row["status"],
        "created_at": row["created_at"],
        "is_owner": users.is_owner(row),
        "is_self": actor is not None and actor["id"] == row["id"],
        "has_profile": _has_profile(row),
    }


@bp.get("/users")
def list_users():
    actor = users.current_user()
    return jsonify(users=[_serialize_user(row, actor) for row in users.all_users()])


@bp.post("/users")
def create_user():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("id") or "").strip()
    display_name = str(body.get("display_name") or "").strip()
    role = body.get("role")

    if role not in users.ROLES:
        return jsonify(error="role must be 'admin' or 'member'"), 400
    try:
        users.validate_user_id(user_id)
    except users.InvalidUserId:
        return jsonify(
            error="invalid id: lowercase letters, digits, '-' or '_' only, "
                  "max 32 characters"
        ), 400
    if user_id == BOOTSTRAP_USER_ID:
        return jsonify(error=f"'{BOOTSTRAP_USER_ID}' is reserved"), 400
    if users.get_user(user_id) is not None:
        return jsonify(error=f"a user '{user_id}' already exists"), 409

    try:
        bridge.run("user-provision", user_id)
    except bridge.BridgeError as exc:
        return jsonify(error=str(exc)), 400

    # The broker's directory/health.db now exist (or already did — idempotent).
    # If this insert itself fails (process crash, disk full), the broker side
    # is already done; retrying this same request succeeds because both
    # user-provision and create_user are idempotent on user_id.
    users.create_user(user_id, display_name, role)
    auth.audit("admin.user_create", detail=user_id)
    return jsonify(ok=True, user=_serialize_user(users.get_user(user_id))), 201


@bp.post("/users/<user_id>/enroll-link")
def enroll_link(user_id):
    target = users.get_user(user_id)
    if target is None:
        return jsonify(error="no such user"), 404
    if target["status"] != "active":
        return jsonify(error="cannot issue an enrollment link for a disabled account"), 400
    token = auth.new_enroll_token(user_id)
    # Audit the issuance only — never the token itself.
    auth.audit("admin.enroll_link_issued", detail=user_id)
    url = f"{current_app.config['PANEL_ORIGIN']}/enroll?token={token}"
    return jsonify(ok=True, url=url, expires_in_seconds=auth.ENROLL_TTL_SECONDS)


@bp.post("/users/<user_id>/status")
def set_status(user_id):
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in users.STATUSES:
        return jsonify(error="status must be 'active' or 'disabled'"), 400

    target = users.get_user(user_id)
    if target is None:
        return jsonify(error="no such user"), 404

    if status == "disabled":
        if users.is_owner(target):
            return jsonify(error="the bootstrap owner cannot be disabled"), 400
        actor = users.current_user()
        if target["id"] == actor["id"]:
            return jsonify(error="you cannot disable your own account"), 400
        if target["role"] == "admin" and target["status"] == "active" \
                and users.active_admin_count() <= 1:
            return jsonify(error="cannot disable the last active admin"), 400

    users.set_status(user_id, status)
    auth.audit(f"admin.user_{status}", detail=user_id)
    return jsonify(ok=True, user=_serialize_user(users.get_user(user_id)))


@bp.post("/users/<user_id>/role")
def set_role(user_id):
    body = request.get_json(silent=True) or {}
    role = body.get("role")
    if role not in users.ROLES:
        return jsonify(error="role must be 'admin' or 'member'"), 400

    target = users.get_user(user_id)
    if target is None:
        return jsonify(error="no such user"), 404
    if users.is_owner(target):
        return jsonify(error="the bootstrap owner's role is fixed"), 400

    if role == "member":
        actor = users.current_user()
        if target["id"] == actor["id"]:
            return jsonify(error="you cannot demote yourself"), 400
        if target["role"] == "admin" and target["status"] == "active" \
                and users.active_admin_count() <= 1:
            return jsonify(error="cannot demote the last active admin"), 400

    users.set_role(user_id, role)
    auth.audit("admin.user_role_change", detail=f"{user_id}:{role}")
    return jsonify(ok=True, user=_serialize_user(users.get_user(user_id)))


@bp.get("/users/<user_id>/profile")
def get_profile(user_id):
    target = users.get_user(user_id)
    if target is None:
        return jsonify(error="no such user"), 404
    if users.is_owner(target):
        return jsonify(error="the bootstrap owner has no per-user profile"), 400
    data, err = read_profile_file(users.user_profile_path(target))
    if err is not None:
        return jsonify(error=f"stored profile is invalid: {err}"), 500
    return jsonify(ok=True, profile=data, keys=sorted(PROFILE_KEYS),
                   json_keys=sorted(PROFILE_JSON_KEYS))


@bp.post("/users/<user_id>/profile")
def set_profile(user_id):
    target = users.get_user(user_id)
    if target is None:
        return jsonify(error="no such user"), 404
    if users.is_owner(target):
        return jsonify(error="the bootstrap owner has no per-user profile"), 400

    body = request.get_json(silent=True)
    data = body.get("profile") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return jsonify(error='expected {"profile": {...}}'), 400
    try:
        validate_profile(data)
    except ProfileError as exc:
        return jsonify(error=str(exc)), 400
    payload = json.dumps(data, sort_keys=True)
    if len(payload.encode("utf-8")) > PROFILE_MAX_BYTES:
        return jsonify(error="profile exceeds the 16 KiB limit"), 400

    try:
        bridge.run("user-profile-write", user_id, stdin=payload)
    except bridge.BridgeError as exc:
        return jsonify(error=str(exc)), 400

    # Key names only — never the values (deployment ground rule).
    auth.audit("admin.profile_update", detail=f"{user_id}:{sorted(data)}")
    return jsonify(ok=True, profile=data)


@bp.post("/users/<user_id>/profile/reset")
def reset_profile(user_id):
    target = users.get_user(user_id)
    if target is None:
        return jsonify(error="no such user"), 404
    if users.is_owner(target):
        return jsonify(error="the bootstrap owner has no per-user profile"), 400

    try:
        bridge.run("user-profile-clear", user_id)
    except bridge.BridgeError as exc:
        return jsonify(error=str(exc)), 400

    auth.audit("admin.profile_reset", detail=user_id)
    return jsonify(ok=True)
