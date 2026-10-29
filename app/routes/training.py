"""Training: today's session + numbers-to-beat, set logging, per-lift
progression/PR, the 7-axis muscle radar.

Simple reads go through db_read (read-only). ENGINE outputs — the muscle
radar, like /dash/scores — go through the bridge to health.py so their math
has exactly one home (determinism law). The one write — logging a set — goes
through the bridge to `health.py log-set`, which tags the row source='ui' so a
later Hevy reimport (source='hevy' only) can't wipe it. e1RM is Epley
(weight × (1 + reps/30)), computed here from raw sets — display math, not a
stored value, same pattern as the Phase-3 dashboard. Fork 2026-10-28: the
quarterly-test surfaces (athletic radar/detail, strength ratios, fitness-test
coverage) were removed — owner decision (docs/PRODUCT_AUTHORITY.md).
"""
import re
from datetime import date

from flask import Blueprint, jsonify, request

from app import bridge, canon, db_read

bp = Blueprint("training", __name__, url_prefix="/api/training")


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _e1rm(weight, reps):
    if weight is None or reps is None:
        return None
    return round(weight * (1 + reps / 30.0), 1)


# ── §3c athletic drill pages: removed 2026-10-28 (owner decision — the
# quarterly-test surface — Athletic Profile, Strength ratios, Quarter progress
# and the mobility/strength-balance lenses — is out of this fork; the engine
# commands stay dormant in health.py). #}

@bp.get("/today")
def today():
    """Scheduled routine + each exercise's target and last-session sets to beat."""
    if (resp := _guard()) is not None:
        return resp
    sched = db_read.query(
        "SELECT routine_name FROM training_schedule WHERE weekday = ?",
        (canon.weekday(),))
    routine = sched[0]["routine_name"] if sched else None
    if not routine or routine == "Rest":
        return jsonify(routine=routine or "Rest", exercises=[])

    exercises = db_read.query(
        "SELECT exercise_title, target_sets, target_reps, target_weight_kg"
        " FROM routines WHERE routine_name = ? ORDER BY ex_order, exercise_title", (routine,))

    # Last logged session per exercise (numbers to beat), one query for the lot.
    titles = [e["exercise_title"] for e in exercises]
    last_by_ex = {}
    if titles:
        placeholders = ",".join("?" * len(titles))
        rows = db_read.query(
            "SELECT h.exercise_title, h.date, h.set_index, h.weight_kg, h.reps"
            " FROM hevy_sets h JOIN ("
            "   SELECT exercise_title, MAX(date) md FROM hevy_sets"
            f"   WHERE COALESCE(set_type,'normal') != 'warmup' AND exercise_title IN ({placeholders})"
            "   GROUP BY exercise_title) last"
            " ON last.exercise_title = h.exercise_title AND last.md = h.date"
            " WHERE COALESCE(h.set_type,'normal') != 'warmup'"
            " ORDER BY h.exercise_title, h.set_index", titles)
        for r in rows:
            last_by_ex.setdefault(r["exercise_title"], {"date": r["date"], "sets": []})
            last_by_ex[r["exercise_title"]]["sets"].append(
                {"weight_kg": r["weight_kg"], "reps": r["reps"]})

    for e in exercises:
        e["last_session"] = last_by_ex.get(e["exercise_title"])
    return jsonify(routine=routine, exercises=exercises)


@bp.get("/progression")
def progression():
    """Per-session best set for one exercise: top weight + best e1RM over time,
    with a running-max PR flag."""
    if (resp := _guard()) is not None:
        return resp
    exercise = (request.args.get("exercise") or "").strip()
    if not exercise:
        return jsonify(error="exercise is required"), 400
    rows = db_read.query(
        "SELECT date, weight_kg, reps FROM hevy_sets"
        " WHERE exercise_title = ? AND COALESCE(set_type,'normal') != 'warmup'"
        " AND weight_kg IS NOT NULL AND reps IS NOT NULL AND date IS NOT NULL"
        " ORDER BY date", (exercise,))
    per_date = {}
    for r in rows:
        e = _e1rm(r["weight_kg"], r["reps"])
        cur = per_date.get(r["date"])
        if cur is None:
            per_date[r["date"]] = {"date": r["date"], "top_weight": r["weight_kg"],
                                   "reps": r["reps"], "e1rm": e}
            continue
        # top_weight is the heaviest set of the day, independent of which set
        # has the best e1RM (a heavier low-rep single can precede a higher-e1RM
        # backoff set — don't let it get overwritten).
        cur["top_weight"] = max(cur["top_weight"], r["weight_kg"])
        if e > cur["e1rm"]:
            cur["e1rm"] = e
            cur["reps"] = r["reps"]
    points = sorted(per_date.values(), key=lambda p: p["date"])
    best = 0.0
    for p in points:
        p["pr"] = p["e1rm"] > best
        best = max(best, p["e1rm"])
    return jsonify(exercise=exercise, points=points)


@bp.get("/overall-progress")
def overall_progress():
    """Combined strength across ALL logged lifts + a weight-to-strength ratio.

    Two deterministic, server-side series (charted client-side):

    Overall strength index — the default Progression view, answering "how
    strong is my body compared to the past" across every lift:
      • Bucket every non-warmup set into its ISO week; per week × exercise keep
        that week's best e1RM (Epley, the same helper the per-lift chart uses).
      • Normalize each exercise to its OWN first-recorded week = 100, so a
        heavy lift can't drown out a light one — each contributes % change.
      • Carry an exercise's last-known e1RM forward over weeks it wasn't
        trained, but ONLY after its first record (never back-fill before it
        existed).
      • Weekly index = mean of the indexed values of the exercises that have
        started by that week. 100 = your starting strength across your lifts.

    Weight-to-strength ratio — relative strength over time:
      • For each week, sum the carried-forward best e1RMs (kg) of the live
        exercises and divide by the body weight recorded nearest at-or-before
        the end of that week.
      • Weeks before any body-weight record → no ratio point; no body weight
        at all → wsr empty + has_weight=False (the card shows an honest empty).

    Weeks are keyed by their Monday (ISO week start) for the time axis.
    """
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query(
        "SELECT date, exercise_title, weight_kg, reps FROM hevy_sets"
        " WHERE COALESCE(set_type,'normal') != 'warmup' AND weight_kg IS NOT NULL"
        " AND reps IS NOT NULL AND date IS NOT NULL AND exercise_title IS NOT NULL"
        " ORDER BY date")

    # best e1RM per (ISO-week-Monday, exercise)
    best = {}
    for r in rows:
        try:
            y, w, _ = date.fromisoformat(r["date"]).isocalendar()
        except ValueError:
            continue    # skip a malformed date rather than crash the chart
        wk = date.fromisocalendar(y, w, 1).isoformat()
        e = _e1rm(r["weight_kg"], r["reps"])
        key = (wk, r["exercise_title"])
        if e is not None and e > best.get(key, 0.0):
            best[key] = e

    by_ex = {}
    for (wk, ex), e in best.items():
        by_ex.setdefault(ex, {})[wk] = e
    weeks = sorted({wk for (wk, _ex) in best})

    # body weights ascending, for the nearest-at-or-before lookup
    weights = [(r["date"], r["weight_kg"]) for r in db_read.query(
        "SELECT date, weight_kg FROM body_metrics"
        " WHERE weight_kg IS NOT NULL AND date IS NOT NULL ORDER BY date")]

    def weight_at_or_before(cutoff):
        chosen = None
        for d, kg in weights:      # ascending — the last one <= cutoff wins
            if d <= cutoff:
                chosen = kg
            else:
                break
        return chosen

    carried, base = {}, {}
    index_points, wsr_points = [], []
    for wk in weeks:
        for ex, wkmap in by_ex.items():
            if wk in wkmap:
                carried[ex] = wkmap[wk]
                base.setdefault(ex, wkmap[wk])   # first-record week = 100
        live = list(carried)                     # exercises started by this week
        vals = [carried[ex] / base[ex] * 100 for ex in live]
        index_points.append({"week": wk, "value": round(sum(vals) / len(vals), 1)})
        y, w, _ = date.fromisoformat(wk).isocalendar()
        cutoff = date.fromisocalendar(y, w, 7).isoformat()   # Sunday of the week
        kg = weight_at_or_before(cutoff)
        if kg:
            total = sum(carried[ex] for ex in live)
            wsr_points.append({"week": wk, "value": round(total / kg, 3),
                               "total_e1rm": round(total, 1), "weight_kg": kg})

    return jsonify(index=index_points, wsr=wsr_points,
                   exercise_count=len(by_ex), has_weight=bool(weights))


@bp.get("/exercises")
def exercises():
    """Distinct exercises that have logged sets — the progression picker offers
    these on top of today's routine, so any past lift can be charted."""
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query(
        "SELECT DISTINCT exercise_title FROM hevy_sets"
        " WHERE exercise_title IS NOT NULL ORDER BY exercise_title")
    return jsonify(exercises=[r["exercise_title"] for r in rows])


@bp.get("/muscle-radar")
def muscle_radar():
    """The 7-axis Muscle Balance payload passes through verbatim from health.py:
    latest completed quarterly strengths, planned weekly distribution, Logged
    7d volume, and mapping diagnostics are computed in the engine, never here."""
    try:
        return jsonify(ok=True, result=bridge.run(
            "muscle-volume", "--by", "group", "--days", "7"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/vtaper")
def vtaper():
    """§3f: V-taper WCR (waist ÷ chest, target ~0.7 — Garza 2017). Engine JSON
    verbatim; the ratio, target, window default and honesty states are all
    health.py's (no flags sent — one source of truth for the default)."""
    try:
        return jsonify(ok=True, result=bridge.run("vtaper"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/muscle-detail")
def muscle_detail():
    """§3b drill-down: exercises driving one radar group + the sub-region
    breakdown (engine-reported `pending_source` until the owner's cited map
    lands). Group vocabulary is validated by health.py — one home."""
    group = (request.args.get("group") or "").strip()
    if not group or group.startswith("-"):
        return jsonify(ok=False, error="a muscle group is required"), 400
    try:
        return jsonify(ok=True, result=bridge.run("muscle-detail", group))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/muscle-map")
def muscle_map():
    """§3f body figure: per-SVG-region display payload for the muscle map.
    Lens vocabulary is validated by health.py (argparse choices — one home);
    the panel only pins the token SHAPE: lowercase-ascii-with-hyphens, so the
    planned lens names (strength-balance, pain, mobility) pass while path
    tricks and flag injection cannot (review: isidentifier() rejected
    hyphens and admitted non-ASCII identifiers)."""
    lens = (request.args.get("lens") or "activation").strip()
    if not re.fullmatch(r"[a-z][a-z-]{0,31}", lens):
        return jsonify(ok=False, error="unknown lens"), 400
    args = ["--lens", lens]
    # §3f Phase 2: strength-balance's Combined|L/R sub-view. Same posture as
    # the lens token: the panel pins SHAPE only, health.py's argparse choices
    # is the vocabulary home (a bad value comes back as a 502 from there).
    side = (request.args.get("side") or "").strip()
    if side:
        if not re.fullmatch(r"[a-z][a-z-]{0,15}", side):
            return jsonify(ok=False, error="unknown side mode"), 400
        args += ["--side-mode", side]
    try:
        return jsonify(ok=True, result=bridge.run("muscle-map", *args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.post("/log-set")
def log_set():
    """Log one strength set via the bridge -> health.py log-set (source='ui')."""
    body = request.get_json(silent=True) or {}
    exercise = (body.get("exercise") or "").strip()
    if not exercise or exercise.startswith("-"):
        return jsonify(ok=False, error="a valid exercise is required"), 400
    args = [exercise]
    try:
        reps = body.get("reps")
        weight = body.get("weight_kg")
        if reps not in (None, ""):
            fr = float(reps)                 # reject fractional reps rather than
            if not fr.is_integer():          # silently truncating (e.g. 3.9 -> 3)
                return jsonify(ok=False, error="reps must be a whole number"), 400
            args += ["--reps", str(int(fr))]
        if weight not in (None, ""):
            args += ["--weight", str(float(weight))]
        if body.get("rpe") not in (None, ""):
            args += ["--rpe", str(float(body["rpe"]))]
    except (TypeError, ValueError):
        return jsonify(ok=False, error="weight/reps/rpe must be numbers"), 400
    if "--reps" not in args and "--weight" not in args:
        return jsonify(ok=False, error="a set needs at least reps or weight"), 400
    if body.get("workout_title"):
        args += ["--workout-title", str(body["workout_title"])[:80]]
    try:
        return jsonify(ok=True, result=bridge.run("log-set", *args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400
