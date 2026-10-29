"""Self-service Profile & targets (fork owner decision 2026-10-29,
docs/PRODUCT_AUTHORITY.md): the logged-in user's own owner_profile/phase/
nutrition_targets/planned_times rows (toolkit's T43/T44/§4b config) and their
own profile.json nutrition overlay, editable from the panel instead of only
the CLI/SSH/MCP path. Every write still crosses the broker (app.bridge.run)
through the newly-allowlisted validated setters, or the self-service
user-profile-merge command for the profile.json nutrition config —
health.py/the broker remain the source of truth; light checks here only give
fast feedback, same style as app/routes/log.py.

Reads come from db_read (the current user's own health.db, read-only) plus
the existing nutrition-targets engine passthrough and users.current_profile()
for the profile.json overlay — never another user's data (db_read/users
resolve strictly off the session, same as every other page)."""
import json
import math
import re

from flask import Blueprint, jsonify, request

from app import bridge, db_read, users
from deploy.bridge_commands import (
    DIET_PHASE_NAMES, MICRO_TARGET_NAMES, PROFILE_JSON_KEYS, ProfileError,
    validate_profile, validate_profile_values,
)

bp = Blueprint("profile", __name__, url_prefix="/api/profile")

OWNER_PROFILE_FIELDS = ("height_cm", "sex", "dob", "activity_fallback")
SEX_VALUES = {"male", "female"}
ACTIVITY_FALLBACK_VALUES = {"sedentary", "light", "moderate", "active", "very_active"}
# DIET_PHASE_NAMES (cut/maintain/bulk) is shared with deploy.bridge_commands —
# phase-set and the profile.json phase_offsets/protein_per_kg keys mean the
# exact same three phases.
NUTRITION_TARGET_NAMES = {"protein_g", "kcal"}
PLANNED_METRICS = {"wake", "bed", "workout", "dose"}

# UI field name -> profile.json env key (deploy.bridge_commands.PROFILE_KEYS).
NUTRITION_CONFIG_FIELDS = {
    "nutrient_targets": "HERMES_NUTRIENT_TARGETS_JSON",
    "phase_offsets": "HERMES_PHASE_OFFSETS_JSON",
    "protein_per_kg": "HERMES_PROTEIN_PER_KG_JSON",
}

OWNER_HAS_NO_PROFILE_JSON = (
    "the bootstrap owner reads installation-wide settings, not a per-user profile.json"
)


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _run(*args, stdin=None):
    try:
        return jsonify(ok=True, result=bridge.run(*args, stdin=stdin))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _num(v):
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return fv if math.isfinite(fv) else None


@bp.get("")
def get_profile():
    guard = _guard()
    if guard:
        return guard
    user = users.current_user()
    is_owner = users.is_owner(user)

    profile_rows = {r["key"]: r["value"] for r in
                    db_read.query("SELECT key, value FROM owner_profile")}
    target_rows = {r["name"]: r["target"] for r in
                   db_read.query("SELECT name, target FROM nutrition_targets")}
    planned_rows = {
        r["metric"]: {"planned": r["planned"], "tolerance_min": r["tolerance_min"]}
        for r in db_read.query("SELECT metric, planned, tolerance_min FROM planned_times")
    }
    # users.current_profile() returns the broker's env-overlay shape: every
    # value a str, with the *_JSON keys re-serialized for a child process's
    # environment (deploy.bridge_commands.profile_env_overlay). Decode those
    # back to objects for JSON API consumers (the JS form fields, not an env).
    raw_overlay = {} if is_owner else users.current_profile()
    overlay = {
        key: (json.loads(value) if key in PROFILE_JSON_KEYS else value)
        for key, value in raw_overlay.items()
    }

    try:
        engine = bridge.run("nutrition-targets")
    except bridge.BridgeError as exc:
        engine = {"status": "refused", "error": str(exc)}

    return jsonify(
        ok=True,
        profile={key: profile_rows.get(key) for key in OWNER_PROFILE_FIELDS},
        phase=profile_rows.get("phase"),
        phase_started=profile_rows.get("phase_started"),
        targets=target_rows,
        planned_times=planned_rows,
        nutrition_config={
            field: overlay.get(env_key)
            for field, env_key in NUTRITION_CONFIG_FIELDS.items()
        },
        per_user_overlay_supported=not is_owner,
        per_user_overlay_reason=None if not is_owner else OWNER_HAS_NO_PROFILE_JSON,
        engine=engine,
        catalog={
            "sex_values": sorted(SEX_VALUES),
            "activity_fallback_values": sorted(ACTIVITY_FALLBACK_VALUES),
            "diet_phases": sorted(DIET_PHASE_NAMES),
            "nutrition_target_names": sorted(NUTRITION_TARGET_NAMES),
            "planned_metrics": sorted(PLANNED_METRICS),
            "micro_target_names": sorted(MICRO_TARGET_NAMES),
        },
    )


@bp.post("/profile")
def set_profile():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not body:
        return jsonify(ok=False, error="nothing to update"), 400
    updates = []
    for key in OWNER_PROFILE_FIELDS:
        if key not in body:
            continue
        value = body[key]
        if key == "height_cm":
            n = _num(value)
            if n is None or not (100 <= n <= 250):
                return jsonify(ok=False, error="height_cm must be between 100 and 250"), 400
            updates.append((key, str(n)))
        elif key == "sex":
            if value not in SEX_VALUES:
                return jsonify(ok=False, error="sex must be male or female"), 400
            updates.append((key, value))
        elif key == "dob":
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return jsonify(ok=False, error="dob must be ISO YYYY-MM-DD"), 400
            updates.append((key, value))
        elif key == "activity_fallback":
            if value not in ACTIVITY_FALLBACK_VALUES:
                return jsonify(ok=False, error="activity_fallback must be one of: "
                               + ", ".join(sorted(ACTIVITY_FALLBACK_VALUES))), 400
            updates.append((key, value))
    if not updates:
        return jsonify(ok=False, error="nothing to update"), 400

    applied = {}
    for key, value in updates:
        try:
            applied[key] = bridge.run("profile-set", key, value)
        except bridge.BridgeError as exc:
            return jsonify(ok=False, error=str(exc), applied=applied), 400
    return jsonify(ok=True, applied=applied)


@bp.post("/phase")
def set_phase():
    body = request.get_json(silent=True) or {}
    phase = body.get("phase")
    if phase not in DIET_PHASE_NAMES:
        return jsonify(ok=False, error="phase must be cut, maintain, or bulk"), 400
    return _run("phase-set", phase)


@bp.post("/targets")
def set_target():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if name not in NUTRITION_TARGET_NAMES:
        return jsonify(ok=False, error="name must be protein_g or kcal"), 400
    target = _num(body.get("target"))
    if target is None or target <= 0:
        return jsonify(ok=False, error="target must be a positive number"), 400
    return _run("nutrition-target-set", name, "--target", str(target))


@bp.post("/planned-time")
def set_planned_time():
    body = request.get_json(silent=True) or {}
    metric = body.get("metric")
    if metric not in PLANNED_METRICS:
        return jsonify(ok=False, error="metric must be one of: "
                       + ", ".join(sorted(PLANNED_METRICS))), 400
    time_value = body.get("time")
    if not isinstance(time_value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
        return jsonify(ok=False, error="time must be HH:MM (24h)"), 400
    args = ["planned-time-set", metric, time_value]
    tolerance = body.get("tolerance")
    if tolerance is not None:
        iv = _num(tolerance)
        if iv is None or iv != int(iv) or not (5 <= int(iv) <= 240):
            return jsonify(ok=False, error="tolerance must be a whole number 5-240"), 400
        args += ["--tolerance", str(int(iv))]
    return _run(*args)


@bp.post("/nutrition-config")
def set_nutrition_config():
    user = users.current_user()
    if users.is_owner(user):
        return jsonify(ok=False, error=OWNER_HAS_NO_PROFILE_JSON), 400

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify(ok=False, error="nothing to update"), 400
    if not set(body) <= set(NUTRITION_CONFIG_FIELDS):
        return jsonify(ok=False, error="unknown nutrition-config field"), 400

    patch = {NUTRITION_CONFIG_FIELDS[field]: value for field, value in body.items()}
    try:
        validate_profile(patch, allow_null=True)
        validate_profile_values({k: v for k, v in patch.items() if v is not None})
    except ProfileError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    return _run("user-profile-merge", stdin=json.dumps(patch))
