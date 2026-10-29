"""Wearable sync API (fork owner decision 2026-10-28; replaces the Models &
spend tests). /api/models/status reads the CURRENT user's own health DB:
collector run records (source_sync_runs) + fitbit daily_metrics freshness.
No bridge, no owner-only gate."""
import json
import pathlib
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import auth as auth_mod
from app import create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()

LOCAL_TODAY = datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _iso(days_ago):
    return (LOCAL_TODAY - timedelta(days=days_ago)).isoformat()


def _now_ts(off=0):
    t = datetime.now(ZoneInfo("America/Sao_Paulo")) + timedelta(seconds=off)
    return t.isoformat(timespec="seconds")


def _stub_db(tmp_path, rows=()):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO source_sync_runs(source, started_at, completed_at, status,"
        " rows_seen, rows_written, error_code) VALUES('google-health', ?, ?, ?, ?, ?, ?)",
        rows)
    con.commit()
    con.close()
    return hdb


def _client(tmp_path, hdb):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    return c


def test_status_shape_with_runs(tmp_path):
    hdb = _stub_db(tmp_path, [
        (_now_ts(), _now_ts(), "success", 58, 58, None),
        (_now_ts(2), _now_ts(3), "failed", None, None, "collector_failed"),
    ])
    d = _client(tmp_path, hdb).get("/api/models/status").get_json()
    assert d["ok"] is True
    assert d["collector_last_run"]["status"] == "failed"
    assert d["collector_last_run"]["error_code"] == "collector_failed"
    assert d["fitbit_latest"] is None
    assert d["fitbit_freshness"] is None
    assert d["fitbit_days_30"] == 0


def test_last_ok_row_is_rows_written_backed(tmp_path):
    hdb = _stub_db(tmp_path, [(_now_ts(), _now_ts(), "success", 45, 45, None)])
    d = _client(tmp_path, hdb).get("/api/models/status").get_json()
    assert d["collector_last_ok"]["rows_written"] == 45


def test_freshness_judgment(tmp_path):
    hdb = _stub_db(tmp_path)
    con = sqlite3.connect(hdb)
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?,'fitbit',58.0)", (_iso(1),))
    con.commit()
    con.close()
    d = _client(tmp_path, hdb).get("/api/models/status").get_json()
    assert d["fitbit_freshness"] == "fresh"
    assert d["fitbit_days_30"] >= 1


def test_empty_db_says_never(tmp_path):
    hdb = _stub_db(tmp_path)
    d = _client(tmp_path, hdb).get("/api/models/status").get_json()
    assert d["fitbit_freshness"] is None
    assert d["collector_last_ok"] is None
    assert d["collector_last_run"] is None


def test_requires_auth(tmp_path):
    hdb = _stub_db(tmp_path)
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    assert app.test_client().get("/api/models/status").status_code == 401
