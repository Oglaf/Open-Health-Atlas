"""Write endpoints — the daily-loop logging surface (Phase 5).

Every endpoint is a POST, CSRF-protected and auth-gated (the app-level gate),
and turns the request into a validated `health.py` call through the bridge.
Nothing here touches SQL: bridge.run() -> hermes-bridge -> health.py. Light
input checks give fast feedback; health.py remains the source of truth for
validation and all math, and its confirmation JSON is echoed back so the write
is visible ("determinism made visible").
"""
import json
import math
import re
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from app import bridge, canon, db_read, users
from app.panel_db import get_db
from toolkit.hermes_insights.catalogs import PAIN_CAUSE_MAP
from toolkit.hermes_insights.physio import (
    PAIN_NRS_CLAMP, PAIN_PATTERN, PAIN_QUALITY, PAIN_RED_FLAGS, PAIN_SIDES,
)

bp = Blueprint("log", __name__, url_prefix="/api/log")

# Explicit mutations: new read commands must never become apparent writes.
ACTIVITY_WRITES = (
    "day-rating", "log", "log-set", "log-food", "eat", "prep", "set-batch",
    "commitment-set", "log-commitment", "checkin", "routine-set",
    "routine-remove", "routine-undo", "schedule-set", "write-note",
    "hypothesis-promote", "log-body-composition",
    "import-hevy-upload",
    # Fork 2026-10-29 (docs/PRODUCT_AUTHORITY.md): Profile & targets setters.
    # user-profile-merge is deliberately absent — its values arrive on stdin
    # only, so the feed (which carries command arguments) would show nothing
    # useful, and the broker never audits profile.json values verbatim.
    "profile-set", "phase-set", "nutrition-target-set", "planned-time-set",
    # Fork 2026-10-29 (docs/PRODUCT_AUTHORITY.md "manual daily capture"):
    # the Daily log page's water/supplement/pain writers.
    "water-add", "supplement-log", "pain-log",
    # Fork 2026-10-31: hand-entered meals on the Nutrition > Meals tab.
    "meal-save", "meal-delete",
)

# Daily log back-dating window: a few days of catch-up, never a historical
# rewrite tool. Validated here as a fast-feedback copy of health.py's own
# date parsing; health.py remains the source of truth.
MAX_LOG_BACK_DAYS = 14


def _valid_log_date(value):
    """One ISO date, today or up to MAX_LOG_BACK_DAYS in the past. None on
    anything else (bad shape, future date, too far back)."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    today = date.fromisoformat(canon.today_iso())
    if parsed > today or parsed < today - timedelta(days=MAX_LOG_BACK_DAYS):
        return None
    return value

# §hevy-panel-import (fork owner decision 2026-10-28, docs/PRODUCT_AUTHORITY.md):
# the bridge's own 8 MiB request-line cap has to fit the whole JSON envelope, so
# the panel enforces a tighter cap on the raw upload itself before it ever
# reaches the socket.
MAX_HEVY_CSV_BYTES = 5 * 1024 * 1024

DAY_RATINGS = {"green", "yellow", "red"}
# subjective 1–5 rating fields the quick-log offers
RATING_FIELDS = ("focus", "energy", "mood", "emotional_regulation",
                 "anxiety", "motivation", "stress")


def _run(*args):
    """Call the bridge, mapping BridgeError to a JSON 400 with a safe message."""
    try:
        return jsonify(ok=True, result=bridge.run(*args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _int_in_range(v, lo, hi):
    """Parse a whole number in [lo, hi]. Rejects (rather than floors) fractional
    input like 124.7 — the server is the trust boundary and must not silently
    coerce a vitals reading (found by review)."""
    try:
        fv = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(fv) or fv != int(fv):
        return None
    iv = int(fv)
    return iv if lo <= iv <= hi else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.post("/day-rating")
def day_rating():
    body = request.get_json(silent=True) or {}
    value = body.get("value")
    if value not in DAY_RATINGS:
        return jsonify(ok=False, error="value must be green, yellow or red"), 400
    args = ["day-rating", value]
    date_raw = body.get("date")
    if date_raw not in (None, ""):
        d = _valid_log_date(date_raw)
        if d is None:
            return jsonify(ok=False, error=(
                "date must be YYYY-MM-DD, not in the future, and within the "
                f"last {MAX_LOG_BACK_DAYS} days")), 400
        args += ["--date", d]
    args += ["--source", "panel-ui"]
    return _run(*args)


@bp.get("/day")
def day_state():
    """The Daily log page's day-rating seed: the current value for a chosen
    date (default today), read-only, current user only (db_read)."""
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    date_raw = request.args.get("date") or canon.today_iso()
    d = _valid_log_date(date_raw)
    if d is None:
        return jsonify(ok=False, error=(
            "date must be YYYY-MM-DD, not in the future, and within the "
            f"last {MAX_LOG_BACK_DAYS} days")), 400
    rows = db_read.query(
        "SELECT day_rating FROM subjective_daily WHERE date = ?", (d,))
    return jsonify(ok=True, date=d, day_rating=(rows[0]["day_rating"] if rows else None))


WORD_STATUSES = {"kept", "partly", "broke"}
CHECKIN_KINDS = {"energy", "focus", "mood"}


@bp.post("/word")
def word():
    """The nightly self-integrity one-tap: did I keep my word to myself today?
    kept / partly / broke (+ optional one-line why) -> health.py log-commitment.
    Without a commitment_id this scores the whole day (id 0); with one it
    scores that specific active commitment. Re-tapping the same day updates
    the row (health.py upserts)."""
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in WORD_STATUSES:
        return jsonify(ok=False, error="status must be kept, partly or broke"), 400
    args = ["log-commitment", status]
    commitment_id = body.get("commitment_id")
    if commitment_id not in (None, "", 0):
        iv = _int_in_range(commitment_id, 1, 1_000_000_000)
        if iv is None:
            return jsonify(ok=False, error="commitment_id must be a positive whole number"), 400
        args += ["--id", str(iv)]
    why = (body.get("why") or "").strip()
    if why:
        if len(why) > 300:
            return jsonify(ok=False, error="why must be under 300 characters"), 400
        args += ["--why", why]
    args += ["--source", "panel-ui"]
    return _run(*args)


@bp.post("/commitment")
def commitment():
    """Create or edit a commitment (health.py commitment-set, upsert by
    name) — the empty-state form for the evening check-in when none exist."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name or len(name) > 120:
        return jsonify(ok=False, error="name is required (max 120 characters)"), 400
    args = ["commitment-set", name]
    for flag, field in (("--identity", "identity"), ("--trigger", "trigger"),
                       ("--floor", "floor"), ("--reward", "reward")):
        value = (body.get(field) or "").strip()
        if value:
            if len(value) > 300:
                return jsonify(ok=False, error=f"{field} must be under 300 characters"), 400
            args += [flag, value]
    return _run(*args)


@bp.get("/evening")
def evening_state():
    """The evening check-in's seed: today's whole-day or per-commitment word
    status, today's energy/focus/mood spot readings, and the user's active
    commitments to pick from — all current-user-only (db_read + commitment-list)."""
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    raw_id = request.args.get("commitment_id", "0")
    if not raw_id.isdigit():
        return jsonify(ok=False, error="commitment_id must be a non-negative integer"), 400
    cid = int(raw_id)
    t = canon.today_iso()
    word_state = None
    if db_read.query("SELECT 1 FROM sqlite_master WHERE type='table'"
                     " AND name='commitments_log'"):
        rows = db_read.query(
            "SELECT status, why FROM commitments_log WHERE date = ? AND commitment_id = ?",
            (t, cid))
        if rows:
            word_state = {"status": rows[0]["status"], "why": rows[0]["why"]}
    checkins = {}
    if db_read.query("SELECT 1 FROM sqlite_master WHERE type='table'"
                     " AND name='checkins'"):
        for row in db_read.query(
                "SELECT kind, value FROM checkins WHERE date = ? ORDER BY id", (t,)):
            checkins[row["kind"]] = row["value"]
    try:
        commitments = bridge.run("commitment-list").get("commitments", [])
    except bridge.BridgeError:
        commitments = []
    return jsonify(ok=True, date=t, commitment_id=cid, word=word_state,
                   checkins=checkins, commitments=commitments)


@bp.post("/checkin")
def checkin():
    """Timed 1-5 spot reading (energy/focus/mood), stamped with the current
    canonical time by health.py — feeds the within-day dose-timing analysis."""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind not in CHECKIN_KINDS:
        return jsonify(ok=False, error="kind must be energy, focus or mood"), 400
    value = _int_in_range(body.get("value"), 1, 5)
    if value is None:
        return jsonify(ok=False, error="value must be a whole number 1-5"), 400
    return _run("checkin", kind, str(value), "--source", "panel-ui")


@bp.post("/vitals")
def vitals():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(ok=False, error="vitals must be an object"), 400
    sys_bp = _int_in_range(body.get("systolic"), 60, 260)
    dia_bp = _int_in_range(body.get("diastolic"), 30, 180)
    if sys_bp is None or dia_bp is None:
        return jsonify(ok=False, error="systolic (60–260) and diastolic (30–180) are required"), 400
    if sys_bp <= dia_bp:
        return jsonify(ok=False, error="systolic must be higher than diastolic"), 400
    args = ["log", "vitals", f"systolic={sys_bp}", f"diastolic={dia_bp}"]
    hr = _int_in_range(body.get("resting_hr"), 30, 220)
    if body.get("resting_hr") not in (None, "") and hr is None:
        return jsonify(ok=False, error="resting_hr must be a whole number 30–220"), 400
    if hr is not None:
        args.append(f"resting_hr={hr}")
    time_value = body.get("time")
    if time_value not in (None, ""):
        if not isinstance(time_value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
            return jsonify(ok=False, error="time must be HH:MM"), 400
        args.append(f"time={time_value}")
    return _run(*args)


@bp.post("/subjective")
def subjective():
    body = request.get_json(silent=True) or {}
    args = ["log", "subjective_daily"]
    for f in RATING_FIELDS:
        if body.get(f) not in (None, ""):
            iv = _int_in_range(body[f], 1, 5)
            if iv is None:
                return jsonify(ok=False, error=f"{f} must be 1–5"), 400
            args.append(f"{f}={iv}")
    dump = (body.get("brain_dump") or "").strip()
    if dump:
        args.append(f"brain_dump={dump}")
    if len(args) == 2:
        return jsonify(ok=False, error="nothing to log"), 400
    return _run(*args)


@bp.post("/body")
def body_metrics():
    body = request.get_json(silent=True) or {}
    args = ["log", "body_metrics"]
    for field, lo, hi in (("weight_kg", 30, 400), ("waist_cm", 30, 250)):
        if body.get(field) not in (None, ""):
            val = _num(body[field])
            if val is None or not (lo <= val <= hi):
                return jsonify(ok=False, error=f"{field} out of range"), 400
            args.append(f"{field}={val}")
    if len(args) == 2:
        return jsonify(ok=False, error="nothing to log"), 400
    return _run(*args)


@bp.post("/body-composition")
def body_composition():
    """Accept one BIA report JSON document (§body-composition, fork owner
    decision 2026-10-27) and forward it verbatim to health.py's validated
    writer via stdin — the same contract the CLI and MCP health_write use.
    health.py owns every validation; this route only shapes the transport."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(ok=False, error="body must be a JSON object"), 400
    try:
        return jsonify(ok=True, result=bridge.run(
            "log-body-composition", stdin=json.dumps(body)))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@bp.post("/hevy-import")
def hevy_import():
    """Accept one Hevy workout-history CSV export (multipart `file`) and
    forward its text verbatim through the guarded `import-hevy-upload` writer
    (§hevy-panel-import, fork owner decision 2026-10-28, PRODUCT_AUTHORITY.md).
    This is the ONE exception to "the panel stays read-only for bulk imports":
    every guard (required columns, at least one data row, every date parses)
    lives in health.py, not here — this route only shapes the transport and
    rejects what would never survive it (missing/empty/oversized/non-UTF-8)."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(ok=False, error="a CSV file is required"), 400
    raw = upload.read(MAX_HEVY_CSV_BYTES + 1)
    if not raw:
        return jsonify(ok=False, error="the uploaded file is empty"), 400
    if len(raw) > MAX_HEVY_CSV_BYTES:
        return jsonify(ok=False, error="file exceeds the 5 MB limit"), 400
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(ok=False, error="file must be UTF-8 text"), 400
    args = ["import-hevy-upload"]
    if request.form.get("dry_run") == "1":
        args.append("--dry-run")
    try:
        return jsonify(ok=True, result=bridge.run(*args, stdin=text))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@bp.post("/water")
def water():
    """Quick +N ml tap -> health.py water-add, which is ADDITIVE (unlike the
    generic `log intake water_ml=` writer, which replaces the day's total —
    a bug a direct `log` call would reintroduce; found in review)."""
    body = request.get_json(silent=True) or {}
    ml = _int_in_range(body.get("water_ml"), 1, 3000)
    if ml is None:
        return jsonify(ok=False, error="water_ml must be a whole number 1-3000"), 400
    args = ["water-add", str(ml)]
    date_raw = body.get("date")
    if date_raw not in (None, ""):
        d = _valid_log_date(date_raw)
        if d is None:
            return jsonify(ok=False, error=(
                "date must be YYYY-MM-DD, not in the future, and within the "
                f"last {MAX_LOG_BACK_DAYS} days")), 400
        args += ["--date", d]
    return _run(*args)


SUPPLEMENT_SOURCE = "manual"


@bp.post("/supplement")
def supplement():
    """One supplement observation against an EXISTING active product
    (health.py's supplement-log resolves the name/id against
    supplement_products; an unknown name is refused there, not here —
    the panel has no product editor, see docs/PRODUCT_AUTHORITY.md)."""
    body = request.get_json(silent=True) or {}
    name = (body.get("supplement") or "").strip()
    if not name:
        return jsonify(ok=False, error="supplement is required"), 400
    args = ["supplement-log", name]
    taken = body.get("taken")
    if taken not in (None, ""):
        iv = _int_in_range(taken, 0, 1)
        if iv is None:
            return jsonify(ok=False, error="taken must be 0 or 1"), 400
        args += ["--taken", str(iv)]
    dose = body.get("dose")
    if dose not in (None, ""):
        dv = _num(dose)
        if dv is None or not (math.isfinite(dv) and 0 <= dv <= 1_000_000):
            return jsonify(ok=False, error="dose must be a number 0-1000000"), 400
        args += ["--dose", str(dv)]
    time_value = body.get("time")
    if time_value not in (None, ""):
        if not isinstance(time_value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
            return jsonify(ok=False, error="time must be HH:MM"), 400
        args += ["--time", time_value]
    date_raw = body.get("date")
    if date_raw not in (None, ""):
        d = _valid_log_date(date_raw)
        if d is None:
            return jsonify(ok=False, error=(
                "date must be YYYY-MM-DD, not in the future, and within the "
                f"last {MAX_LOG_BACK_DAYS} days")), 400
        args += ["--date", d]
    note = (body.get("note") or "").strip()
    if note:
        if len(note) > 2000:
            return jsonify(ok=False, error="note must be under 2000 characters"), 400
        args += ["--note", note]
    args += ["--source", SUPPLEMENT_SOURCE]
    return _run(*args)


PAIN_SOURCE = "manual"
PAIN_INTENSITY_MIN, PAIN_INTENSITY_MAX = PAIN_NRS_CLAMP


@bp.get("/pain-catalog")
def pain_catalog():
    """The enums the pain form needs — read from the same toolkit catalogs
    health.py's pain-log validates against, so the form never hardcodes a
    second, driftable copy."""
    return jsonify(
        regions=sorted(PAIN_CAUSE_MAP),
        sides=sorted(PAIN_SIDES),
        qualities=sorted(PAIN_QUALITY),
        patterns=sorted(PAIN_PATTERN),
        red_flags=sorted(PAIN_RED_FLAGS),
        intensity_min=PAIN_INTENSITY_MIN,
        intensity_max=PAIN_INTENSITY_MAX,
    )


@bp.post("/pain")
def pain():
    body = request.get_json(silent=True) or {}
    region = (body.get("region") or "").strip().lower()
    if region not in PAIN_CAUSE_MAP:
        return jsonify(ok=False, error="unknown pain region"), 400
    intensity = _int_in_range(body.get("intensity"), PAIN_INTENSITY_MIN, PAIN_INTENSITY_MAX)
    if intensity is None:
        return jsonify(ok=False, error=(
            f"intensity must be a whole number {PAIN_INTENSITY_MIN}-{PAIN_INTENSITY_MAX}")), 400
    args = ["pain-log", region, "--intensity", str(intensity)]
    side = (body.get("side") or "").strip().lower()
    if side:
        if side not in PAIN_SIDES:
            return jsonify(ok=False, error="side must be one of: " + ", ".join(sorted(PAIN_SIDES))), 400
        args += ["--side", side]
    quality = (body.get("quality") or "").strip().lower()
    if quality:
        if quality not in PAIN_QUALITY:
            return jsonify(ok=False, error="quality must be one of: " + ", ".join(sorted(PAIN_QUALITY))), 400
        args += ["--quality", quality]
    pattern = (body.get("pattern") or "").strip().lower()
    if pattern:
        if pattern not in PAIN_PATTERN:
            return jsonify(ok=False, error="pattern must be one of: " + ", ".join(sorted(PAIN_PATTERN))), 400
        args += ["--pattern", pattern]
    flags = body.get("flags")
    if flags not in (None, "", []):
        if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
            return jsonify(ok=False, error="flags must be a list of strings"), 400
        bad = [f for f in flags if f.strip().lower() not in PAIN_RED_FLAGS]
        if bad:
            return jsonify(ok=False, error="unknown red flag(s): " + ", ".join(bad)), 400
        args += ["--flags", ",".join(f.strip().lower() for f in flags)]
    date_raw = body.get("date")
    if date_raw not in (None, ""):
        d = _valid_log_date(date_raw)
        if d is None:
            return jsonify(ok=False, error=(
                "date must be YYYY-MM-DD, not in the future, and within the "
                f"last {MAX_LOG_BACK_DAYS} days")), 400
        args += ["--date", d]
    note = (body.get("note") or "").strip()
    if note:
        if len(note) > 2000:
            return jsonify(ok=False, error="note must be under 2000 characters"), 400
        args += ["--note", note]
    args += ["--source", PAIN_SOURCE]
    return _run(*args)


@bp.get("/pain-recent")
def pain_recent():
    """The pain section's own last-5 feed — current user only, voided rows
    excluded (the deletion law: a mistaken row is voided, never deleted, and
    every read filters it out)."""
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    if not db_read.query("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='pain_log'"):
        return jsonify(ok=True, items=[])
    rows = db_read.query(
        "SELECT date, region, side, intensity, quality, pattern, flags, note"
        " FROM pain_log WHERE voided = 0 ORDER BY id DESC LIMIT 5")
    return jsonify(ok=True, items=rows)


@bp.get("/recent")
def recent():
    """Activity feed: panel-originated write attempts (panel.db's audit_log),
    newest first. Reads the panel's own state DB, not the health DB."""
    events = ["bridge." + command for command in ACTIVITY_WRITES]
    # panel.db is shared by every user: the feed carries command arguments
    # (readings, notes), so it must only ever show the caller's own rows.
    user = users.current_user()
    if user is None:
        return jsonify(error="unauthenticated"), 401
    rows = get_db().execute(
        "SELECT ts, event, detail FROM audit_log"
        f" WHERE user_id = ? AND event IN ({','.join('?' for _ in events)})"
        " ORDER BY id DESC LIMIT 20",
        [user["id"], *events],
    )
    items = []
    for row in rows:
        outcome = None
        try:
            detail = json.loads(row["detail"])
            if isinstance(detail, dict):
                outcome = detail.get("outcome")
        except (TypeError, ValueError):
            pass
        status = "unconfirmed"
        if outcome == "exit:0":
            status = "succeeded"
        elif isinstance(outcome, str) and (
            re.fullmatch(r"exit:-?\d+", outcome) or outcome.startswith("refused-")
        ):
            status = "failed"
        items.append({**dict(row), "status": status})
    return jsonify(items=items)
