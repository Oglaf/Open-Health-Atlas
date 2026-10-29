"""Core page routes and user-visible navigation targets."""
from datetime import date

from flask import Blueprint, abort, jsonify, redirect, render_template, url_for

from app import db_read, users

bp = Blueprint("core", __name__)

@bp.get("/training")
def training():
    return render_template("training.html")


@bp.get("/training/muscle/<group>")
def muscle_detail(group):
    """§3b drill-down page shell. The group string is validated by the engine
    when the page's JS calls the API (single vocabulary home); a bad group
    renders the engine's error, and Jinja autoescaping keeps the echo safe."""
    return render_template("muscle_detail.html", group=group)


@bp.get("/training/pain")
def pain_workspace():
    """Pain workspace: report a painful spot to the coach + see the cited
    physio loop. READ-ONLY for writes — the chat is the only capture path."""
    return render_template("report_workspace.html", lens="pain", label="Pain")


@bp.get("/consistency")
def consistency():
    """Consistency pillar (design v-consistency): kept-word ring, streaks,
    habit ledger, follow-through trend, 12-month day-ratings calendar."""
    return render_template("consistency.html")


@bp.get("/consistency/habits")
def consistency_habits():
    """Habit-ledger drill sub-page (task-35, design image 11). Composes
    /api/dash/habits-ledger-detail (db_read only) into per-habit status
    cards (Established/Building/Rebuilding/Fragile — deterministic, see that
    route's docstring) plus a "ready for a new habit?" gate. No range dd:
    the classification windows (all-time + a fixed 30d) are fixed by the
    rules, not user-selectable."""
    return render_template("consistency_habits.html")


@bp.get("/recovery")
def recovery():
    """Recovery pillar (design v-recovery): sleep + HRV/RHR KPIs and trends
    (real, from daily_metrics). HR/RHR live here by product contract (dropped
    from Body). Readiness KPI has no engine yet — the readiness drill (task-30)
    renders an honest skeleton (real HRV/RHR/sleep component rows, pending
    score/gauge)."""
    return render_template("recovery.html")


# task-30 drill sub-pages: the four Recovery KPI cards each link to
# /recovery/<metric>. Off-list slugs 404 rather than render a dead page.
RECOVERY_METRICS = ("sleep", "hrv", "rhr", "readiness")
RECOVERY_LABELS = {"sleep": "Sleep", "hrv": "HRV", "rhr": "Resting HR"}


@bp.get("/recovery/<metric>")
def recovery_detail(metric):
    """Recovery KPI drill sub-page (task-30). `readiness` is an honest skeleton
    (no engine); sleep/hrv/rhr share one real-data template + JS, parameterized
    by the metric. Off-list slugs 404. Data comes from /api/dash/recovery-detail
    (SELECT-only db_read composition — no bridge, no write path)."""
    metric = metric.lower()
    if metric not in RECOVERY_METRICS:
        abort(404)
    if metric == "readiness":
        return render_template("readiness_detail.html")
    return render_template("recovery_detail.html",
                           metric=metric, label=RECOVERY_LABELS[metric])


@bp.get("/plans")
def plans():
    """Legacy Plans URL; the current consolidated surface is Body."""
    return redirect(url_for("core.training"))


@bp.get("/program")
def program():
    """Legacy Program URL; validated plan APIs remain, while the current UI is Body."""
    return redirect(url_for("core.training"))


@bp.get("/integrations")
def integrations():
    """Integrations & privacy settings page (design image 31, task-36): a
    read-only "Connected sources" status board (Telegram bot, Hevy, Wearable,
    Bloodwork lab) plus a short factual privacy note. Real data comes from
    /api/integrations/sources (per-user db_read SELECTs — no bridge reads
    on this deployment); this page has
    no controls of its own, so the Fitbit re-auth button stays on Models &
    spend rather than being duplicated here."""
    return render_template("integrations.html")


@bp.get("/vault-notes")
def vault_notes():
    """Vault notes settings page (design: Settings drawer → General). Edits the
    four whitelisted Markdown notes with a diff preview; the write still goes
    through /api/plans/note → the bridge → health.py write-note (unchanged)."""
    return render_template("vault_notes.html")


@bp.get("/nutrition")
def nutrition():
    return render_template("nutrition.html")


@bp.get("/nutrition/recipe/<rid>")
def recipe_detail(rid):
    """Recipe detail sub-page (T33 drill-down). Validates the id against the
    real recipes table so an unknown slug 404s instead of rendering a dead
    shell; the page's JS then loads /api/nutrition/recipe/<rid> for the full
    macro/micro breakdown. If the health DB is down we still render the shell
    (the page shows its own unavailable state) rather than mask the outage as
    a 404."""
    if db_read.available():
        rows = db_read.query("SELECT name FROM recipes WHERE recipe_id = ?", (rid,))
        if not rows:
            abort(404)
        name = rows[0]["name"]
    else:
        name = None
    return render_template("recipe_detail.html", rid=rid, name=name)


@bp.get("/models")
def models():
    """Wearable sync page (fork owner decision 2026-10-28): the Models &
    spend page keeps only data that exists on this deployment — the URL /
    endpoint name are unchanged so old bookmarks still work, and the page
    renders the wearable-sync card for ANY logged-in user."""
    return render_template("wearable_sync.html")


@bp.get("/export")
def export():
    return render_template("export.html")


@bp.get("/chat")
def chat():
    """Legacy Chat URL. The panel chat lives with Insight Explorer, which this
    fork removed (owner decision 2026-10-28); no agent is installed on this
    deployment, so /chat no longer has a destination and 404s."""
    abort(404)


@bp.get("/data")
def data():
    return render_template("data.html")



@bp.get("/admin")
def admin():
    """Admin panel (docs/MULTI_USER.md "Admin UI"): add and manage family
    members from the web UI — no CLI or assistant required. Admin role only;
    `require_login` (app/__init__.py) already requires an active session, so
    this only adds the role check (a logged-in member gets 403, not the
    page)."""
    user = users.current_user()
    if user is None or user["role"] != "admin":
        abort(403)
    return render_template("admin.html")


@bp.get("/healthz")
def healthz():
    return jsonify(ok=True)


@bp.get("/")
def dashboard():
    return render_template("dashboard.html", today=date.today().isoformat())
