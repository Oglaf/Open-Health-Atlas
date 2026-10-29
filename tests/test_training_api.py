"""Phase 6: training endpoints — today's session, progression/PR, the 7-axis
muscle radar (engine pass-through), and set logging via a stubbed bridge."""
import pathlib
import sqlite3
from datetime import date, timedelta

import pytest

from app import auth as auth_mod
from app import bridge, create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()


def _seed(hdb):
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    # a routine scheduled for EVERY weekday so "today" always returns it
    con.executemany("INSERT INTO routines VALUES(?,?,?,?,?,?)", [
        ("R", "Front Squat", 1, 3, 8, 60.0),
        ("R", "Deadlift (Barbell)", 2, 3, 5, 80.0),
    ])
    con.executemany("INSERT INTO training_schedule VALUES(?,?)",
                    [(d, "R") for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")])
    con.executemany("INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)", [
        ("Front Squat", "Quads", 1.0), ("Front Squat", "Glutes", 0.5),
        ("Deadlift (Barbell)", "Hamstrings", 1.0),
    ])
    # Front Squat history within the last 7 days so muscle-volume (7d) has data
    d0 = date.today().isoformat()
    d3 = (date.today() - timedelta(days=3)).isoformat()
    con.executemany(
        "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source) VALUES(?,?,?,?,?,?,?)",
        [(dd, "Front Squat", i, "normal", w, 8, "hevy")
         for dd, w in ((d3, 60.0), (d0, 65.0)) for i in (1, 2, 3)])
    con.commit()
    con.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    _seed(hdb)
    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a))) or {"ok": True}))
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    c.hdb = str(hdb)
    return c


def test_today_session_with_numbers_to_beat(client):
    data = client.get("/api/training/today").get_json()
    assert data["routine"] == "R"
    titles = [e["exercise_title"] for e in data["exercises"]]
    assert titles == ["Front Squat", "Deadlift (Barbell)"]
    fs = data["exercises"][0]
    assert fs["target_sets"] == 3 and fs["target_weight_kg"] == 60.0
    assert fs["last_session"]["sets"] == [{"weight_kg": 65.0, "reps": 8}] * 3  # latest session
    assert data["exercises"][1]["last_session"] is None  # deadlift never logged


def test_today_session_numbers_to_beat_includes_null_set_type(client):
    """task-50 item 5: set_type=NULL means a working set (toolkit/health.py
    convention), not a warmup, so a bare `set_type != 'warmup'` comparison
    silently excludes it (SQL NULL comparisons are never true). "Numbers to
    beat" must include a NULL-set_type latest session (r3's overall_progress
    COALESCE fix, applied here too)."""
    con = sqlite3.connect(client.hdb)
    con.execute("DELETE FROM hevy_sets WHERE exercise_title = 'Front Squat'")
    d0 = date.today().isoformat()
    con.executemany(
        "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source) VALUES(?,?,?,?,?,?,?)",
        [(d0, "Front Squat", i, None, 70.0, 8, "hevy") for i in (1, 2, 3)])
    con.commit(); con.close()
    data = client.get("/api/training/today").get_json()
    fs = data["exercises"][0]
    assert fs["last_session"]["sets"] == [{"weight_kg": 70.0, "reps": 8}] * 3


def test_progression_and_pr_flag(client):
    p = client.get("/api/training/progression?exercise=Front Squat").get_json()
    assert [pt["top_weight"] for pt in p["points"]] == [60.0, 65.0]
    # e1RM = w*(1+8/30); both sessions are new bests as weight climbs
    assert all(pt["pr"] for pt in p["points"])
    assert p["points"][1]["e1rm"] == round(65.0 * (1 + 8 / 30), 1)


def test_progression_top_weight_is_heaviest_not_best_e1rm_set(client):
    """A heavier low-rep single before a higher-e1RM backoff set must not lose
    the true top weight (review finding)."""
    con = sqlite3.connect(client.hdb)
    con.executemany(
        "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source) VALUES(?,?,?,?,?,?,?)",
        [("2026-05-01", "Bench", 1, "normal", 120.0, 3, "ui"),   # e1RM 132.0, top weight
         ("2026-05-01", "Bench", 2, "normal", 110.0, 8, "ui")])  # e1RM 139.3, best e1RM
    con.commit(); con.close()
    p = client.get("/api/training/progression?exercise=Bench").get_json()
    pt = p["points"][0]
    assert pt["top_weight"] == 120.0                       # heaviest set kept
    assert pt["e1rm"] == round(110.0 * (1 + 8 / 30), 1)    # best e1RM still from backoff


def test_progression_includes_null_set_type(client):
    """task-50 item 5: same NULL-set_type-excluded defect as /today, in the
    /progression query's `set_type != 'warmup'` filter."""
    con = sqlite3.connect(client.hdb)
    con.executemany(
        "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source) VALUES(?,?,?,?,?,?,?)",
        [("2026-05-01", "Row", 1, None, 50.0, 30, "hevy")])   # reps=30 -> e1RM = 2x weight
    con.commit(); con.close()
    p = client.get("/api/training/progression?exercise=Row").get_json()
    assert p["points"] and p["points"][0]["top_weight"] == 50.0
    assert p["points"][0]["e1rm"] == 100.0


def test_progression_requires_exercise(client):
    assert client.get("/api/training/progression").status_code == 400


def test_overall_progress_default_fixture(client):
    """Combined index on the seeded fixture: only Front Squat is logged, so the
    index normalizes to itself (first week = 100), and with no body weight
    seeded the weight:strength series is honestly empty."""
    d = client.get("/api/training/overall-progress").get_json()
    assert d["exercise_count"] == 1
    assert d["index"] and d["index"][0]["value"] == 100.0   # first week = start
    assert d["has_weight"] is False and d["wsr"] == []


def test_overall_progress_index_and_wsr_math(client):
    """Two lifts across two ISO weeks with known e1RMs verify the whole
    computation: per-exercise normalization to its own first week, carry-forward,
    the mean-of-live index, and the weight:strength ratio. A third lift with
    set_type=NULL is included too — NULL means a working set (toolkit/health.py
    convention), not a warmup, so it must count."""
    con = sqlite3.connect(client.hdb)
    con.execute("DELETE FROM hevy_sets")
    con.execute("DELETE FROM body_metrics")
    # reps=30 → Epley factor is exactly 2.0, so e1RM = 2×weight (no rounding noise)
    con.executemany(
        "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source) VALUES(?,?,?,?,?,?,?)",
        [("2026-06-01", "A", 1, "normal", 50.0, 30, "hevy"),   # week 1: e1RM 100
         ("2026-06-08", "A", 1, "normal", 55.0, 30, "hevy"),   # week 2: e1RM 110
         ("2026-06-08", "B", 1, "normal", 30.0, 30, "hevy"),   # week 2: B starts, e1RM 60
         ("2026-06-08", "C", 1, None, 40.0, 30, "hevy")])      # week 2: C starts (NULL set_type), e1RM 80
    con.executemany(
        "INSERT INTO body_metrics(date, weight_kg, source) VALUES(?,?,?)",
        [("2026-06-01", 80.0, "manual"), ("2026-06-08", 82.0, "manual")])
    con.commit(); con.close()
    d = client.get("/api/training/overall-progress").get_json()
    # week1: only A live → 100. week2: A=110%, B starts=100%, C starts=100% → mean 103.3.
    assert [p["value"] for p in d["index"]] == [100.0, 103.3]
    assert d["exercise_count"] == 3 and d["has_weight"] is True
    # WSR: week1 total 100 ÷ 80 kg; week2 total (110+60+80)=250 ÷ 82 kg
    assert d["wsr"][0]["value"] == round(100.0 / 80.0, 3)
    assert d["wsr"][1]["value"] == round(250.0 / 82.0, 3)
    assert d["wsr"][1]["total_e1rm"] == 250.0


def test_exercises_list(client):
    ex = client.get("/api/training/exercises").get_json()["exercises"]
    assert ex == ["Front Squat"]  # only logged lift


def test_muscle_radar_passes_engine_json_through(client, monkeypatch):
    """All three 7-axis sources are computed by health.py (muscle-volume --by
    group) and passed through verbatim — the panel never replicates the
    quarterly-strength or volume rollups."""
    canned = {"by": "group", "window_days": 7,
              "axes": ["Chest", "Back", "Arms", "Shoulders", "Legs", "Core", "Glutes"],
              "current_strengths": {"status": "insufficient_data", "groups": {},
                                    "distribution_pct": {}},
              "combined": {"Chest": 3.0}, "planned": {"Chest": 6.0},
              "logged_distribution_pct": {"Chest": 100.0},
              "planned_distribution_pct": {"Chest": 100.0},
              "unmapped": [], "left_right": {"status": "insufficient_data"}}
    calls = []
    monkeypatch.setattr(bridge, "run",
                        lambda sub, *a, **k: (calls.append((sub, list(a))) or canned))
    api = client.get("/api/training/muscle-radar").get_json()
    assert api["ok"] is True and api["result"] == canned
    assert calls == [("muscle-volume", ["--by", "group", "--days", "7"])]


def test_muscle_radar_reports_bridge_failure(client, monkeypatch):
    def boom(*a, **k):
        raise bridge.BridgeError("broker down")
    monkeypatch.setattr(bridge, "run", boom)
    r = client.get("/api/training/muscle-radar")
    assert r.status_code == 502
    assert "broker down" in r.get_json()["error"]


def test_vtaper_passes_through(client, monkeypatch):
    canned = {"insufficient_data": True, "target_wcr": 0.7}
    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a))) or canned))
    api = client.get("/api/training/vtaper").get_json()
    assert api["ok"] and api["result"] == canned
    assert calls == [("vtaper", [])]     # no flags — health.py owns the default window


def test_muscle_detail_passes_through_and_guards(client, monkeypatch):
    canned = {"group": "Glutes", "exercises": [], "sub_regions": {"status": "pending_source"}}
    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a))) or canned))
    api = client.get("/api/training/muscle-detail?group=Glutes").get_json()
    assert api["ok"] and api["result"] == canned
    assert calls == [("muscle-detail", ["Glutes"])]
    assert client.get("/api/training/muscle-detail").status_code == 400
    assert client.get("/api/training/muscle-detail?group=--evil").status_code == 400


def test_muscle_map_passes_through_and_guards(client, monkeypatch):
    canned = {"lens": "activation", "regions": {}, "non_muscle": []}
    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a))) or canned))
    api = client.get("/api/training/muscle-map").get_json()
    assert api["ok"] and api["result"] == canned
    assert calls == [("muscle-map", ["--lens", "activation"])]
    calls.clear()
    # hyphenated tokens are legal shape — the planned lens names
    # (strength-balance, pain, mobility) must reach health.py, whose argparse
    # choices is the vocabulary home (unknown ones come back as a 502 there)
    client.get("/api/training/muscle-map?lens=strength-balance")
    assert calls == [("muscle-map", ["--lens", "strength-balance"])]
    calls.clear()
    # Phase 4: the mobility lens token is legal shape and rides through
    client.get("/api/training/muscle-map?lens=mobility")
    assert calls == [("muscle-map", ["--lens", "mobility"])]
    calls.clear()
    # non-token shapes never reach the bridge
    for bad in ("..%2Fhack", "a%20b", "A", "-x", "%C3%A0ctivation"):
        assert client.get("/api/training/muscle-map?lens=" + bad).status_code == 400, bad
    assert calls == []
    # §3f Phase 2: the side param rides through as --side-mode (vocabulary is
    # health.py's argparse choices — one home; the panel pins token shape only)
    client.get("/api/training/muscle-map?lens=strength-balance&side=lr")
    assert calls == [("muscle-map", ["--lens", "strength-balance",
                                    "--side-mode", "lr"])]
    calls.clear()
    for bad in ("..%2Fhack", "a%20b", "L", "-x"):
        assert client.get("/api/training/muscle-map?lens=strength-balance&side="
                          + bad).status_code == 400, bad
    assert calls == []


def test_training_page_has_muscle_figure_cards(client):
    r = client.get("/training")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    for marker in (  # Task B: one "Body map" cockpit card with a shared lens
                   # bar; the front/back figures are labelled columns beneath it
                   # (was two "Body map — front/back" cards) so they stay aligned.
                   ">Body map ", "Front · anterior", "Back · posterior",
                   'id="mf-front"',
                   'id="mf-back"', "vendor/body-muscles/body-muscles.umd.min.js",
                   "js/muscle-figure.js",
                   # §3g Phase 3: the pain lens tab + the read-only physio-loop
                   # side card (populated by training.js when the lens is active)
                   'data-tab="pain"', 'id="mf-loop"'):
        assert marker in html, marker


def test_muscle_balance_graphs_use_one_toggleable_three_source_overlay(client):
    body = client.get("/training").get_data(as_text=True)
    dash = client.get("/").get_data(as_text=True)
    assert 'id="muscle-view"' not in body
    assert 'id="dash-muscle-view"' not in dash
    scripts = (
        (ROOT / "app" / "static" / "js" / "training.js").read_text()
        + (ROOT / "app" / "static" / "js" / "dashboard.js").read_text()
    )
    for name in ("Current strengths", "Planned", "Logged 7d"):
        assert scripts.count(name) >= 2
    assert scripts.count('legendselectchanged') >= 2
    assert "planned_distribution_pct" in scripts
    assert "logged_distribution_pct" in scripts


def test_muscle_detail_page_has_scoped_figure(client):
    html = client.get("/training/muscle/Glutes").get_data(as_text=True)
    for marker in ("On the body — front", "On the body — back", 'id="mf-front"',
                   "Relative sub-region strength theory", 'id="md-theory-body"',
                   "Personal-history estimate", "vendor/body-muscles/body-muscles.umd.min.js",
                   "js/muscle-figure.js"):
        assert marker in html, marker


def test_muscle_detail_page_renders(client):
    r = client.get("/training/muscle/Glutes")
    assert r.status_code == 200 and b"sub-regions" in r.data
    # hostile group strings must come back escaped (Jinja autoescape): the
    # raw payload must not appear outside the page's own legit <script src>
    # tags, and the escaped form MUST appear where the group is echoed
    r = client.get("/training/muscle/%3Cscript%3Ex")
    assert r.status_code == 200
    assert b"<script>x" not in r.data
    assert b"&lt;script&gt;x" in r.data


# ── Task C: Pain / Mobility dedicated workspace pages (REPORT-lens deep link) ──
# These are READ-ONLY-for-writes workspaces: their own body map (point at the
# spot), a coach chat (the ONLY capture path — reuses /api/chat/send; the panel
# never writes pain/mobility), and the read-only physio-loop / mobility state.

def test_pain_workspace_page_renders(client):
    html = client.get("/training/pain").get_data(as_text=True)
    for marker in (
        'data-lens="pain"',                 # the workspace JS reads the fixed lens
        'id="mf-front"', 'id="mf-back"',    # its own body map (front + back)
        'id="rw-selcount" aria-live="polite"',
        'id="mf-loop"',                     # the read-only physio-loop state card
        "vendor/body-muscles/body-muscles.umd.min.js",
        "js/muscle-figure.js", "js/physio-render.js", "js/report_workspace.js",
    ):
        assert marker in html, marker
    # determinism: a dedicated workspace must NOT ship any capture form control
    # (pain is reported by telling the coach, never a panel field). The chat
    # input is the coach path; there must be no pain-value / NRS input.
    assert 'name="nrs"' not in html and 'type="number"' not in html


def test_workspace_accepts_region_query_without_server_echo(client):
    # the pre-selected region travels in ?region= and is read CLIENT-side only
    # (never echoed into the server-rendered HTML) — so a hostile region value
    # is inert: the page still renders and the raw value never lands in markup.
    r = client.get("/training/pain?region=%3Cscript%3Ex")
    assert r.status_code == 200
    assert b"<script>x" not in r.data


def test_new_training_reads_report_bridge_failure(client, monkeypatch):
    def boom(*a, **k):
        raise bridge.BridgeError("broker down")
    monkeypatch.setattr(bridge, "run", boom)
    for url in ("/api/training/vtaper",
                "/api/training/muscle-detail?group=Glutes",
                "/api/training/muscle-map"):
        assert client.get(url).status_code == 502
    # fork 2026-10-28: the quarterly-test endpoints are 404 (route removed)
    for gone in ("/api/training/athletic-radar", "/api/training/strength-ratios",
                 "/api/training/fitness-tests", "/api/training/mobility"):
        assert client.get(gone).status_code == 404


# ── §3c athletic drill endpoint + pages ──────────────────────────────────────

def _mock_radar(monkeypatch, scores):
    """Stub the bridge so athletic-radar returns a canned scores map."""
    canned = {"axes": ["strength", "endurance", "speed", "balance", "flexibility"],
              "scores": scores, "stale_after_days": 120}
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: canned)


def test_log_set_maps_to_bridge(client):
    r = client.post("/api/training/log-set", json={"exercise": "Front Squat", "weight_kg": 67.5, "reps": 8})
    assert r.status_code == 200
    sub, args = client.calls[-1]
    assert sub == "log-set" and args[0] == "Front Squat"
    assert "--reps" in args and "8" in args and "67.5" in args


def test_log_set_rejects_flaglike_exercise(client):
    assert client.post("/api/training/log-set", json={"exercise": "--reps", "reps": 8}).status_code == 400
    assert client.calls == []


def test_log_set_needs_reps_or_weight(client):
    assert client.post("/api/training/log-set", json={"exercise": "Front Squat"}).status_code == 400


def test_log_set_rejects_fractional_reps(client):
    # a non-UI JSON client sending 3.9 reps must be rejected, not truncated to 3
    r = client.post("/api/training/log-set", json={"exercise": "Front Squat", "reps": 3.9})
    assert r.status_code == 400
    assert client.calls == []


def test_training_endpoints_require_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/training/today").status_code == 401
    assert app.test_client().get("/api/training/overall-progress").status_code == 401
    assert app.test_client().post("/api/training/log-set", json={"exercise": "x", "reps": 8}).status_code == 401
