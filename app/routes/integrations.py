"""Wearable-sync status board (fork owner decision 2026-10-28; replaces
Models & spend). Read-only rows over the CURRENT user's own health database:
Hevy training sync (last hevy_sets date) and the scheduled Google Health
collector (last run record from source_sync_runs + latest fitbit
daily_metrics date). Apple is retired per RETIRED_SOURCES and reported
separately so it can never drag or prop up the live status.

Every chip below is a DETERMINISTIC derivation, documented here, never a
hardcoded "Connected" — stale or missing data says so (owner ground rule).
"""
import json

from flask import Blueprint, jsonify

from app import canon, db_read

bp = Blueprint("integrations", __name__, url_prefix="/api/integrations")

# "Connected" freshness windows, in days. Hevy: 2-3 sessions/week is normal,
# so a run without a hard rest week still reads Connected at 14 days.
# Wearable: the collector syncs roughly daily, so 3 days catches a real
# outage without flagging a single day the watch wasn't opened.
HEVY_FRESH_DAYS = 14
WEARABLE_FRESH_DAYS = 3

# Product decision: the Apple wearable pipeline is retired (device
# replaced; last apple daily_metrics row 2026-05-31). Apple's MAX(date) is
# frozen going forward, so it must never drag or prop up the live wearable
# status — occasional manual archive imports may still land (roughly
# monthly/quarterly), so apple_latest keeps reading the real DB date.
RETIRED_SOURCES = {"apple": "2026-05-31"}


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _max_date(sql):
    rows = db_read.query(sql)
    return rows[0]["m"] if rows and rows[0]["m"] else None


def _status_for(latest_date, fresh_days):
    """connected/stale/none against a fresh-days cutoff, computed on the
    canonical configured day (app.canon) — never SQLite's UTC 'now'."""
    if not latest_date:
        return "none"
    return "connected" if latest_date >= canon.days_ago_iso(fresh_days) else "stale"


@bp.get("/sources")
def sources():
    """Composes the Connected-sources card: Hevy training sync (last
    hevy_sets date) and Wearable (the scheduled Google Health collector's
    last run record + latest fitbit daily_metrics date)."""
    if (resp := _guard()) is not None:
        return resp

    hevy_latest = _max_date("SELECT MAX(date) AS m FROM hevy_sets")
    apple_latest = _max_date("SELECT MAX(date) AS m FROM daily_metrics WHERE source = 'apple'")
    fitbit_latest = _max_date("SELECT MAX(date) AS m FROM daily_metrics WHERE source = 'fitbit'")
    # Apple is retired (see RETIRED_SOURCES) — the combined wearable status
    # comes from fitbit alone now, even when apple rows exist and are newer.
    wearable_latest = fitbit_latest

    last_run = db_read.query(
        "SELECT completed_at, status, error_code, details_json"
        " FROM source_sync_runs WHERE source = 'google-health'"
        " ORDER BY id DESC LIMIT 1",
    )
    run = last_run[0] if last_run else None
    details = None
    if run is not None and run["details_json"]:
        try:
            details = json.loads(run["details_json"])
        except (TypeError, ValueError):
            details = None

    return jsonify(
        hevy={
            "status": _status_for(hevy_latest, HEVY_FRESH_DAYS),
            "latest_date": hevy_latest,
        },
        wearable={
            "status": _status_for(wearable_latest, WEARABLE_FRESH_DAYS),
            "latest_date": wearable_latest,
            "collector_last_run": run["completed_at"] if run else None,
            "collector_status": run["status"] if run else None,
            "collector_error": run["error_code"] if run else None,
            "collector_details": details,
            "apple_latest": apple_latest,
            "fitbit_latest": fitbit_latest,
            "apple": {
                "status": "retired",
                "latest_date": apple_latest,
                "since": RETIRED_SOURCES["apple"],
                "note": f"retired {RETIRED_SOURCES['apple']} — wearable replaced; "
                        "archive imports may still land occasionally",
            },
        },
    )
