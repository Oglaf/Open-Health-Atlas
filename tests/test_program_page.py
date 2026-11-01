"""Program page: the /program route renders for a signed-in user only, the
plan it reads is per-user, and the edit endpoints it uses reject bad input
before reaching the (mocked) bridge. All data here is fictional."""
import sqlite3
import pathlib

import pytest

from app import auth as auth_mod
from app import bridge, canon, create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()

# (routine, exercise, order, sets, reps_or_minutes, kg)
PLAN = [
    ("Demo Lower", "Demo Squat", 1, 3, 10, 60.0),
    ("Demo Lower", "Demo Treadmill", 2, 1, 30, None),
    ("Demo Upper", "Demo Press", 1, 3, 8, 30.0),
    ("Demo Upper", "Demo Crunch", 2, 3, 15, None),
]
SCHEDULE = [("Mon", "Demo Lower"), ("Wed", "Demo Upper")]  # Tue and Thu-Sun unset


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO routines VALUES(?,?,?,?,?,?)", PLAN)
    con.executemany("INSERT INTO training_schedule VALUES(?,?)", SCHEDULE)
    con.execute("INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps)"
                " VALUES('2026-01-05','Demo Squat',1,57.5,10)")
    con.commit(); con.close()

    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a))) or {"ok": True}))
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb), "VAULT_DIR": str(tmp_path),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_program_page_renders(client):
    r = client.get("/program")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for marker in ('id="prog-today"', 'id="prog-week-grid"', 'id="prog-routines"',
                   'id="prog-edit-toggle"', "js/program.js"):
        assert marker in html, marker
    # Read-only until the toggle is pressed: the toggle starts unpressed.
    assert 'aria-pressed="false"' in html


def test_program_page_requires_a_session(app):
    anon = app.test_client()
    r = anon.get("/program")
    assert r.status_code in (301, 302, 401)
    assert "Demo" not in r.get_data(as_text=True)


def test_plan_the_page_reads_matches_the_fixture(client):
    p = client.get("/api/plans/program").get_json()
    assert p["routine_names"] == ["Demo Lower", "Demo Upper"]
    assert [(e["exercise_title"], e["target_sets"], e["target_reps"], e["target_weight_kg"])
            for e in p["routines"]["Demo Lower"]] == [
        ("Demo Squat", 3, 10, 60.0), ("Demo Treadmill", 1, 30, None)]
    # Unscheduled days are simply absent; the page renders them as Rest.
    assert p["schedule"] == {"Mon": "Demo Lower", "Wed": "Demo Upper"}


def test_today_card_source(client, monkeypatch):
    monkeypatch.setattr(canon, "weekday", lambda: "Mon")
    t = client.get("/api/training/today").get_json()
    assert t["routine"] == "Demo Lower"
    squat = t["exercises"][0]
    assert squat["exercise_title"] == "Demo Squat"
    assert squat["last_session"]["sets"] == [{"weight_kg": 57.5, "reps": 10}]
    assert t["exercises"][1]["last_session"] is None

    monkeypatch.setattr(canon, "weekday", lambda: "Sat")
    assert client.get("/api/training/today").get_json() == {"routine": "Rest", "exercises": []}


def test_editor_posts_validate_before_the_bridge(client):
    post = client.post
    assert post("/api/plans/routine-set", json={"routine": "", "exercise": "X"}).status_code == 400
    assert post("/api/plans/routine-set", json={"routine": "Demo Lower", "exercise": ""}).status_code == 400
    assert post("/api/plans/routine-set", json={"routine": "Demo Lower", "exercise": "X", "sets": 21}).status_code == 400
    assert post("/api/plans/routine-set", json={"routine": "Demo Lower", "exercise": "X", "weight": "heavy"}).status_code == 400
    assert post("/api/plans/routine-remove", json={"routine": "Demo Lower"}).status_code == 400
    assert post("/api/plans/schedule-set", json={"weekday": "Someday", "routine": "Demo Lower"}).status_code == 400
    assert client.calls == []


def test_editor_accepts_rest_and_new_routine(client):
    assert client.post("/api/plans/schedule-set", json={"weekday": "Tue", "routine": "Rest"}).status_code == 200
    assert client.calls[-1] == ("schedule-set", ["Tue", "Rest"])
    r = client.post("/api/plans/routine-set", json={
        "routine": "Demo New", "exercise": "Demo Row", "sets": 3, "reps": 12, "weight": 20, "order": 1})
    assert r.status_code == 200
    sub, args = client.calls[-1]
    assert sub == "routine-set" and args[:2] == ["Demo New", "Demo Row"]
