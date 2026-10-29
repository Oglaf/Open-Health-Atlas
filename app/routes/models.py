"""Wearable sync (fork owner decision 2026-10-28): the old Models & spend
page (active model switch, OpenRouter spend/budget, Fitbit re-auth token
state — all hermesctl/Hermes-agent features that do not exist on this
containerized deployment) is reduced to a wearable-sync status page that
works for ANY logged-in user, reading only the user's own health database:
the scheduled Google Health collector's last run record (source_sync_runs,
written by `collector-run-record`) plus daily_metrics fitbit freshness.
No bridge calls, no owner-only gate.
"""
from flask import Blueprint, jsonify

from app import canon, db_read

bp = Blueprint("models", __name__, url_prefix="/api/models")


def _guard():
    if not db_read.available():
        return jsonify(ok=False, error="health database not available"), 503
    return None


def _row(query, params=()):
    rows = db_read.query(query, params)
    return rows[0] if rows else None


@bp.get("/status")
def status():
    """One call, current user's own DB:
    - last successful collector run / last run of any status (source_sync_runs)
    - latest fitbit daily_metrics date + days-with-data in the last 30
    - freshness judgment (fresh/stale) against the same 3-day window
      app.routes.integrations uses; "never" is latest_date=None.
    """
    if (resp := _guard()) is not None:
        return resp
    last_ok = _row(
        "SELECT completed_at, coverage_from, coverage_to, rows_written"
        " FROM source_sync_runs WHERE source = 'google-health' AND status = 'success'"
        " ORDER BY id DESC LIMIT 1")
    last_any = _row(
        "SELECT completed_at, status, error_code FROM source_sync_runs"
        " WHERE source = 'google-health' ORDER BY id DESC LIMIT 1")
    latest = _row("SELECT MAX(date) AS m FROM daily_metrics WHERE source = 'fitbit'")
    latest_date = latest["m"] if latest else None
    days30 = _row(
        "SELECT COUNT(DISTINCT date) AS n FROM daily_metrics"
        " WHERE source = 'fitbit' AND date >= ?", (canon.days_ago_iso(30),))
    freshness = None
    if latest_date:
        freshness = "fresh" if latest_date >= canon.days_ago_iso(3) else "stale"
    return jsonify(
        ok=True,
        collector_last_ok=dict(last_ok) if last_ok else None,
        collector_last_run=dict(last_any) if last_any else None,
        fitbit_latest=latest_date,
        fitbit_freshness=freshness,
        fitbit_days_30=(days30["n"] if days30 else 0),
    )
