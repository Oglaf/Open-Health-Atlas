"""Integrations & privacy — /api/integrations/sources (fork 2026-10-28 shape:
Hevy + Wearable only; collector runs from source_sync_runs in the CURRENT
user's own health DB; no bridge/Telegram/Bloodwork rows). Auth, shape, and
the deterministic connected/stale/none derivations over a real seeded or
empty health.db."""
import pathlib
import sqlite3
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

import pytest

from app import auth as auth_mod
from app import create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()

# Same canonical-day anchor as test_dash_api.py: routes compare against the
# canonical timezone day (app.canon), not SQLite's UTC date('now').
LOCAL_TODAY = datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _iso(days_ago):
    return (LOCAL_TODAY - timedelta(days=days_ago)).isoformat()


def _run_now(completed_at, status="success", error_code=None, details=None):
    return {
        "completed_at": completed_at, "status": status, "error_code": error_code,
        "details_json": json.dumps(details or {}),
    }


def _make_app(tmp_path, hdb):
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    return client


@pytest.fixture()
def fresh_client(tmp_path):
    """All sources have data inside their freshness windows + a collector run."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps, source) "
                "VALUES(?, 'Squat', 60, 8, 'hevy')", (_iso(2),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'apple', 60.0)", (_iso(1),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'fitbit', 58.0)", (_iso(1),))
    con.execute("INSERT INTO source_sync_runs(source, started_at, completed_at, status,"
                " rows_seen, rows_written) VALUES('google-health', ?, ?, 'success', 10, 9)",
                (_iso(1), _iso(0)))
    con.commit()
    con.close()
    return _make_app(tmp_path, hdb)


def _empty_client(tmp_path):
    """Schema exists but every relevant table is empty — honest 'none' everywhere."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return _make_app(tmp_path, hdb)


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/integrations/sources").status_code == 401


def test_shape_and_freshness(fresh_client):
    d = fresh_client.get("/api/integrations/sources").get_json()
    assert set(d) == {"hevy", "wearable"}
    assert d["hevy"]["status"] == "connected"
    assert d["hevy"]["latest_date"] == _iso(2)
    # Wearable status/date derive from fitbit ONLY (the live source) —
    # apple's date no longer participates even though it exists and is newer.
    w = d["wearable"]
    assert w["status"] == "connected"
    assert w["latest_date"] == _iso(1)
    assert w["fitbit_latest"] == _iso(1)
    assert w["apple_latest"] == _iso(1)
    assert w["collector_last_run"] == _iso(0)
    assert w["collector_status"] == "success"
    assert w["apple"]["status"] == "retired"
    assert w["apple"]["since"] == "2026-05-31"
    assert "2026-05-31" in w["apple"]["note"]


def test_stale_data_says_stale(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps, source) "
                "VALUES(?, 'Squat', 60, 8, 'hevy')", (_iso(40),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'fitbit', 58.0)", (_iso(10),))
    con.execute("INSERT INTO source_sync_runs(source, started_at, completed_at, status,"
                " error_code) VALUES('google-health', ?, ?, 'failed', 'collector_failed')",
                (_iso(10), _iso(10)))
    con.commit()
    con.close()
    client = _make_app(tmp_path, hdb)
    d = client.get("/api/integrations/sources").get_json()
    assert d["hevy"]["status"] == "stale"
    assert d["wearable"]["status"] == "stale"
    assert d["wearable"]["fitbit_latest"] == _iso(10)
    assert d["wearable"]["apple_latest"] is None
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] is None
    assert d["wearable"]["collector_status"] == "failed"
    assert d["wearable"]["collector_error"] == "collector_failed"


def test_collector_failed_run_does_not_break_freshness(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'fitbit', 58.0)", (_iso(1),))
    con.commit()
    con.close()
    client = _make_app(tmp_path, hdb)
    d = client.get("/api/integrations/sources").get_json()
    # Data is fresh even though the collector has never run (manually imported).
    assert d["wearable"]["status"] == "connected"
    assert d["wearable"]["collector_last_run"] is None


def test_empty_db_is_honest_not_fabricated(tmp_path):
    d = _empty_client(tmp_path).get("/api/integrations/sources").get_json()
    assert d["hevy"] == {"status": "none", "latest_date": None}
    assert d["wearable"]["status"] == "none"
    assert d["wearable"]["apple_latest"] is None
    assert d["wearable"]["fitbit_latest"] is None
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] is None
    assert d["wearable"]["collector_last_run"] is None


def test_health_db_unavailable(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    assert client.get("/api/integrations/sources").status_code == 503
