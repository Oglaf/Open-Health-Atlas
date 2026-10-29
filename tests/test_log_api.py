"""Phase 5: the daily-loop write endpoints. A recording stub replaces the bridge
so we assert the exact health.py argv each endpoint produces + input validation."""
import io
import json
import pathlib
import sqlite3

import pytest

from app import auth as auth_mod
from app import bridge, create_app
from app.panel_db import get_db

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def db_client(tmp_path, monkeypatch):
    """Like `client`, plus a real (empty, schema'd) health.db so the Daily
    log page's GET state routes (db_read) have something to answer from."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    calls = []
    stdins = []

    def fake_run(subcmd, *args, **kw):
        calls.append((subcmd, list(args)))
        stdins.append(kw.get("stdin"))
        if subcmd == "commitment-list":
            return {"commitments": []}
        return {"ok": True}

    monkeypatch.setattr(bridge, "run", fake_run)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    c = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    c.set_cookie(auth_mod.SESSION_COOKIE, token)
    c.calls = calls
    c.stdins = stdins
    c.hdb = hdb
    return c


@pytest.fixture()
def client(tmp_path, monkeypatch):
    calls = []
    stdins = []

    def fake_run(subcmd, *args, **kw):
        calls.append((subcmd, list(args)))
        stdins.append(kw.get("stdin"))
        return {"ok": True}

    monkeypatch.setattr(bridge, "run", fake_run)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
    })
    c = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    c.set_cookie(auth_mod.SESSION_COOKIE, token)
    c.calls = calls
    c.stdins = stdins
    return c


def test_endpoints_require_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().post("/api/log/day-rating", json={"value": "green"}).status_code == 401


def test_day_rating(client):
    r = client.post("/api/log/day-rating", json={"value": "green"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert client.calls[-1] == ("day-rating", ["green", "--source", "panel-ui"])


def test_day_rating_rejects_bad_value(client):
    assert client.post("/api/log/day-rating", json={"value": "purple"}).status_code == 400
    assert client.calls == []  # never reached the bridge


def test_day_rating_with_date(client):
    r = client.post("/api/log/day-rating", json={"value": "yellow", "date": "2026-10-20"})
    assert r.status_code == 200
    assert client.calls[-1] == ("day-rating", ["yellow", "--date", "2026-10-20", "--source", "panel-ui"])


def test_day_rating_rejects_future_and_too_old_dates(client):
    assert client.post("/api/log/day-rating",
                       json={"value": "green", "date": "2099-01-01"}).status_code == 400
    assert client.post("/api/log/day-rating",
                       json={"value": "green", "date": "2026-09-01"}).status_code == 400
    assert client.post("/api/log/day-rating",
                       json={"value": "green", "date": "not-a-date"}).status_code == 400
    assert client.calls == []


def test_day_state_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/log/day").status_code == 401


def test_day_state_reads_todays_rating_then_a_backdated_one(db_client):
    r = db_client.get("/api/log/day")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["day_rating"] is None

    con = sqlite3.connect(db_client.hdb)
    con.execute("INSERT INTO subjective_daily(date, day_rating) VALUES ('2026-10-20', 2)")
    con.commit()
    con.close()
    r = db_client.get("/api/log/day?date=2026-10-20")
    assert r.get_json() == {"ok": True, "date": "2026-10-20", "day_rating": 2}


def test_day_state_rejects_future_and_too_old_dates(db_client):
    assert db_client.get("/api/log/day?date=2099-01-01").status_code == 400
    assert db_client.get("/api/log/day?date=2026-09-01").status_code == 400


def test_vitals_maps_to_health_py(client):
    r = client.post("/api/log/vitals", json={"systolic": 124, "diastolic": 80, "resting_hr": 62})
    assert r.status_code == 200
    assert client.calls[-1] == ("log", ["vitals", "systolic=124", "diastolic=80", "resting_hr=62"])


def test_vitals_hr_optional(client):
    client.post("/api/log/vitals", json={"systolic": 118, "diastolic": 76})
    assert client.calls[-1] == ("log", ["vitals", "systolic=118", "diastolic=76"])


def test_vitals_out_of_range_rejected(client):
    assert client.post("/api/log/vitals", json={"systolic": 400, "diastolic": 80}).status_code == 400
    assert client.post("/api/log/vitals", json={"systolic": 120}).status_code == 400
    assert client.calls == []


def test_vitals_systolic_must_exceed_diastolic(client):
    r = client.post("/api/log/vitals", json={"systolic": 90, "diastolic": 150})
    assert r.status_code == 400 and "higher than diastolic" in r.get_json()["error"]
    assert client.calls == []


def test_vitals_rejects_fractional_not_floors(client):
    # A non-UI JSON client posting 124.7 must be rejected, not silently floored.
    r = client.post("/api/log/vitals", json={"systolic": 124.7, "diastolic": 80})
    assert r.status_code == 400
    assert client.calls == []


def test_vitals_rejects_invalid_supplied_values_without_partial_write(client):
    for field, value in (
        ("resting_hr", "oops"), ("resting_hr", 250), ("resting_hr", 64.5),
        ("systolic", "Infinity"), ("diastolic", "NaN"),
        ("systolic", 10 ** 500),
        ("resting_hr", "-Infinity"), ("time", "25:30"), ("time", "08:10extra"),
    ):
        response = client.post("/api/log/vitals", json={
            "systolic": 120, "diastolic": 80, field: value,
        })
        assert response.status_code == 400, (field, value)
        assert response.get_json()["ok"] is False
    assert client.post("/api/log/vitals", json=[120, 80]).status_code == 400
    assert client.calls == []
    response = client.post("/api/log/vitals", json={
        "systolic": 120, "diastolic": 80, "resting_hr": 64, "time": "08:10",
    })
    assert response.status_code == 200
    assert client.calls == [("log", ["vitals", "systolic=120", "diastolic=80",
                                     "resting_hr=64", "time=08:10"])]


def test_subjective_only_provided_fields(client):
    client.post("/api/log/subjective", json={"focus": 4, "mood": 3})
    assert client.calls[-1] == ("log", ["subjective_daily", "focus=4", "mood=3"])


def test_subjective_rating_range(client):
    assert client.post("/api/log/subjective", json={"focus": 9}).status_code == 400
    assert client.post("/api/log/subjective", json={}).status_code == 400  # nothing to log
    assert client.calls == []


def test_subjective_brain_dump(client):
    client.post("/api/log/subjective", json={"brain_dump": "rough afternoon"})
    assert client.calls[-1] == ("log", ["subjective_daily", "brain_dump=rough afternoon"])


def test_body(client):
    client.post("/api/log/body", json={"weight_kg": 63.4, "waist_cm": 72})
    assert client.calls[-1] == ("log", ["body_metrics", "weight_kg=63.4", "waist_cm=72.0"])


def test_water(client):
    """water-add is ADDITIVE, unlike the generic `log intake` writer which
    replaces the day's total — this route must call water-add, not log."""
    client.post("/api/log/water", json={"water_ml": 250})
    assert client.calls[-1] == ("water-add", ["250"])


def test_water_with_date(client):
    client.post("/api/log/water", json={"water_ml": 500, "date": "2026-10-20"})
    assert client.calls[-1] == ("water-add", ["500", "--date", "2026-10-20"])


def test_water_rejects_bad_values(client):
    assert client.post("/api/log/water", json={"water_ml": 0}).status_code == 400
    assert client.post("/api/log/water", json={"water_ml": 3001}).status_code == 400
    assert client.post("/api/log/water", json={"water_ml": 250.5}).status_code == 400
    assert client.post("/api/log/water", json={"water_ml": 250, "date": "2099-01-01"}).status_code == 400
    assert client.post("/api/log/water", json={"water_ml": 250, "date": "2026-09-01"}).status_code == 400
    assert client.calls == []


HEVY_CSV_BYTES = (
    b"start_time,exercise_title,set_index,weight_kg\n2026-07-01,Front Squat,1,60\n"
)


def test_hevy_import_forwards_csv_as_stdin(client):
    """§hevy-panel-import: the route shapes no CSV fields itself — health.py's
    import-hevy-upload owns every validation; the panel only carries the text."""
    r = client.post("/api/log/hevy-import", data={
        "file": (io.BytesIO(HEVY_CSV_BYTES), "hevy.csv"), "dry_run": "0",
    }, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert client.calls[-1] == ("import-hevy-upload", [])
    assert client.stdins[-1] == HEVY_CSV_BYTES.decode("utf-8")


def test_hevy_import_dry_run_passes_flag(client):
    r = client.post("/api/log/hevy-import", data={
        "file": (io.BytesIO(HEVY_CSV_BYTES), "hevy.csv"), "dry_run": "1",
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert client.calls[-1] == ("import-hevy-upload", ["--dry-run"])


def test_hevy_import_accepts_bom(client):
    r = client.post("/api/log/hevy-import", data={
        "file": (io.BytesIO(b"\xef\xbb\xbf" + HEVY_CSV_BYTES), "hevy.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert client.stdins[-1] == HEVY_CSV_BYTES.decode("utf-8")   # BOM stripped


def test_hevy_import_rejects_missing_file(client):
    r = client.post("/api/log/hevy-import", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert client.calls == []


def test_hevy_import_rejects_empty_file(client):
    r = client.post("/api/log/hevy-import", data={
        "file": (io.BytesIO(b""), "hevy.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert client.calls == []


def test_hevy_import_rejects_oversize_file(client):
    big = io.BytesIO(b"x" * (5 * 1024 * 1024 + 1))
    r = client.post("/api/log/hevy-import", data={
        "file": (big, "hevy.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert client.calls == []


def test_hevy_import_rejects_non_utf8_file(client):
    r = client.post("/api/log/hevy-import", data={
        "file": (io.BytesIO(b"start_time,exercise_title\n\xff\xfe,Squat\n"), "hevy.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert client.calls == []


def test_hevy_import_surfaces_bridge_errors(client, monkeypatch):
    def refuse(subcmd, *args, **kw):
        raise bridge.BridgeError("hevy CSV is missing required column(s): set_index")

    monkeypatch.setattr(bridge, "run", refuse)
    r = client.post("/api/log/hevy-import", data={
        "file": (io.BytesIO(HEVY_CSV_BYTES), "hevy.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "set_index" in r.get_json()["error"]


def test_body_composition_forwards_the_document_verbatim_as_stdin(client):
    """§body-composition: the route shapes no fields itself — health.py owns
    every validation; the panel only carries the document to the writer."""
    report = {"date": "2026-02-10", "components": [
        {"component": "weight", "value_kg": 84.4},
    ]}
    r = client.post("/api/log/body-composition", json=report)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert client.calls[-1] == ("log-body-composition", [])
    assert json.loads(client.stdins[-1]) == report


def test_body_composition_rejects_non_object_body(client):
    r = client.post("/api/log/body-composition", json=[1, 2, 3])
    assert r.status_code == 400
    assert client.calls == []


def test_body_composition_surfaces_bridge_errors(client, monkeypatch):
    def refuse(subcmd, *args, **kw):
        raise bridge.BridgeError("date is required")

    monkeypatch.setattr(bridge, "run", refuse)
    r = client.post("/api/log/body-composition", json={"bogus": True})
    assert r.status_code == 400
    assert r.get_json() == {"ok": False, "error": "date is required"}


def test_bridge_error_becomes_400(client, monkeypatch):
    def boom(*a, **k):
        raise bridge.BridgeError("health.py said no")
    monkeypatch.setattr(bridge, "run", boom)
    r = client.post("/api/log/day-rating", json={"value": "red"})
    assert r.status_code == 400 and "health.py said no" in r.get_json()["error"]


def test_recent_feed_reads_panel_audit(client):
    with client.application.app_context():
        db = get_db()
        # Reads must not crowd write attempts out; failures remain explicit.
        for i in range(22):
            db.execute("INSERT INTO audit_log(ts,event,detail,user_id) VALUES(?,?,?,'owner')", (
                i, "bridge.eat", json.dumps({"args": [f"meal-{i}"], "outcome": "exit:0"}),
            ))
        for i in range(30):
            for event, detail in (
                ("bridge.labs", {"outcome": "exit:0"}),
                ("bridge.outcome-associations", {"outcome": "exit:0"}),
            ):
                db.execute("INSERT INTO audit_log(ts,event,detail,user_id) VALUES(?,?,?,'owner')", (
                    100 + i, event, json.dumps(detail),
                ))
        for event, detail in (
            ("bridge.eat", {"outcome": "exit:1"}),
            ("bridge.eat", {"outcome": "transport-error:TimeoutError"}),
            ("bridge.prep", {"outcome": "refused-client-flag"}),
            ("bridge.log", {"args": ["legacy-unconfirmed"]}),
        ):
            db.execute("INSERT INTO audit_log(ts,event,detail,user_id) VALUES(?,?,?,'owner')", (
                150, event, json.dumps(detail),
            ))
        for detail in ("not JSON", "null", "[]"):
            db.execute("INSERT INTO audit_log(ts,event,detail,user_id) VALUES(?,?,?,'owner')",
                       (200, "bridge.eat", detail))
        db.commit()
    r = client.get("/api/log/recent")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 20
    assert [item["status"] for item in items] == [
        *(["unconfirmed"] * 4), "failed", "unconfirmed", "failed",
        *(["succeeded"] * 13),
    ]
    assert [item["ts"] for item in items[7:]] == list(range(21, 8, -1))
    assert {item["event"] for item in items} <= {"bridge.eat", "bridge.prep", "bridge.log"}


def test_word_maps_to_log_commitment(client):
    r = client.post("/api/log/word", json={"status": "kept"})
    assert r.status_code == 200
    assert client.calls[-1] == ("log-commitment", ["kept", "--source", "panel-ui"])
    r = client.post("/api/log/word", json={"status": "broke", "why": "  late scroll  "})
    assert r.status_code == 200
    assert client.calls[-1] == ("log-commitment", ["broke", "--why", "late scroll",
                                                "--source", "panel-ui"])


def test_word_rejects_bad_status_and_long_why(client):
    assert client.post("/api/log/word", json={"status": "meh"}).status_code == 400
    assert client.post("/api/log/word",
                       json={"status": "kept", "why": "x" * 301}).status_code == 400
    assert client.calls == []


def test_word_with_commitment_id(client):
    r = client.post("/api/log/word", json={"status": "kept", "commitment_id": 3})
    assert r.status_code == 200
    assert client.calls[-1] == ("log-commitment", ["kept", "--id", "3", "--source", "panel-ui"])
    assert client.post("/api/log/word",
                       json={"status": "kept", "commitment_id": -1}).status_code == 400
    assert client.post("/api/log/word",
                       json={"status": "kept", "commitment_id": 1.5}).status_code == 400


def test_commitment_set_maps_to_commitment_set(client):
    r = client.post("/api/log/commitment", json={
        "name": "no-scroll", "identity": "a focused person",
        "trigger": "picking up my phone", "floor": "name it out loud",
    })
    assert r.status_code == 200
    assert client.calls[-1] == ("commitment-set", [
        "no-scroll", "--identity", "a focused person",
        "--trigger", "picking up my phone", "--floor", "name it out loud",
    ])


def test_commitment_set_requires_name(client):
    assert client.post("/api/log/commitment", json={}).status_code == 400
    assert client.post("/api/log/commitment", json={"name": "  "}).status_code == 400
    assert client.calls == []


def test_evening_state_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/log/evening").status_code == 401


def test_evening_state_empty_then_populated(db_client):
    r = db_client.get("/api/log/evening")
    assert r.status_code == 200
    d = r.get_json()
    assert d["word"] is None and d["checkins"] == {} and d["commitments"] == []

    from app import canon
    con = sqlite3.connect(db_client.hdb)
    today = canon.today_iso()
    con.execute("INSERT INTO commitments_log(date, commitment_id, status, why)"
               " VALUES (?, 0, 'kept', 'stayed off my phone')", (today,))
    con.execute("INSERT INTO checkins(date, time, kind, value) VALUES (?, '21:00', 'mood', 4)",
               (today,))
    con.commit()
    con.close()
    r = db_client.get("/api/log/evening")
    d = r.get_json()
    assert d["word"] == {"status": "kept", "why": "stayed off my phone"}
    assert d["checkins"] == {"mood": 4}


def test_evening_state_rejects_bad_commitment_id(db_client):
    assert db_client.get("/api/log/evening?commitment_id=-1").status_code == 400
    assert db_client.get("/api/log/evening?commitment_id=abc").status_code == 400


def test_checkin_maps_and_validates(client):
    r = client.post("/api/log/checkin", json={"kind": "energy", "value": 4})
    assert r.status_code == 200
    assert client.calls[-1] == ("checkin", ["energy", "4", "--source", "panel-ui"])
    assert client.post("/api/log/checkin", json={"kind": "vibes", "value": 3}).status_code == 400
    assert client.post("/api/log/checkin", json={"kind": "focus", "value": 6}).status_code == 400
    assert client.post("/api/log/checkin", json={"kind": "focus", "value": 3.5}).status_code == 400


def test_supplement_maps_to_supplement_log(client):
    r = client.post("/api/log/supplement", json={
        "supplement": "creatine", "taken": 1, "dose": 5, "time": "08:00",
        "date": "2026-10-20", "note": "with breakfast",
    })
    assert r.status_code == 200
    assert client.calls[-1] == ("supplement-log", [
        "creatine", "--taken", "1", "--dose", "5.0", "--time", "08:00",
        "--date", "2026-10-20", "--note", "with breakfast", "--source", "manual",
    ])


def test_supplement_minimal_call(client):
    r = client.post("/api/log/supplement", json={"supplement": "zinc"})
    assert r.status_code == 200
    assert client.calls[-1] == ("supplement-log", ["zinc", "--source", "manual"])


def test_supplement_rejects_bad_values(client):
    assert client.post("/api/log/supplement", json={}).status_code == 400
    assert client.post("/api/log/supplement",
                       json={"supplement": "zinc", "taken": 2}).status_code == 400
    assert client.post("/api/log/supplement",
                       json={"supplement": "zinc", "dose": "nope"}).status_code == 400
    assert client.post("/api/log/supplement",
                       json={"supplement": "zinc", "time": "25:00"}).status_code == 400
    assert client.post("/api/log/supplement",
                       json={"supplement": "zinc", "note": "x" * 2001}).status_code == 400
    assert client.calls == []


def test_pain_catalog_exposes_toolkit_enums(client):
    r = client.get("/api/log/pain-catalog")
    assert r.status_code == 200
    d = r.get_json()
    assert "low-back" in d["regions"]
    assert set(d["sides"]) >= {"left", "right", "central"}
    assert d["intensity_min"] == 0 and d["intensity_max"] == 10


def test_pain_maps_to_pain_log(client):
    r = client.post("/api/log/pain", json={
        "region": "low-back", "intensity": 4, "side": "left", "quality": "ache",
        "pattern": "morning", "flags": ["night-pain"], "date": "2026-10-20",
        "note": "stiff on waking",
    })
    assert r.status_code == 200
    assert client.calls[-1] == ("pain-log", [
        "low-back", "--intensity", "4", "--side", "left", "--quality", "ache",
        "--pattern", "morning", "--flags", "night-pain", "--date", "2026-10-20",
        "--note", "stiff on waking", "--source", "manual",
    ])


def test_pain_minimal_call(client):
    r = client.post("/api/log/pain", json={"region": "shoulder", "intensity": 0})
    assert r.status_code == 200
    assert client.calls[-1] == ("pain-log", ["shoulder", "--intensity", "0", "--source", "manual"])


def test_pain_rejects_bad_values(client):
    assert client.post("/api/log/pain", json={"region": "elbow", "intensity": 4}).status_code == 400
    assert client.post("/api/log/pain", json={"region": "low-back"}).status_code == 400
    assert client.post("/api/log/pain",
                       json={"region": "low-back", "intensity": 11}).status_code == 400
    assert client.post("/api/log/pain",
                       json={"region": "low-back", "intensity": 4, "side": "top"}).status_code == 400
    assert client.post("/api/log/pain", json={
        "region": "low-back", "intensity": 4, "flags": ["not-a-flag"],
    }).status_code == 400
    assert client.calls == []


def test_pain_recent_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/log/pain-recent").status_code == 401


def test_pain_recent_empty_then_populated(db_client):
    r = db_client.get("/api/log/pain-recent")
    assert r.status_code == 200 and r.get_json()["items"] == []

    con = sqlite3.connect(db_client.hdb)
    con.execute("INSERT INTO pain_log(date, region, side, intensity, source)"
               " VALUES ('2026-10-20', 'low-back', 'left', 4, 'manual')")
    con.execute("INSERT INTO pain_log(date, region, side, intensity, source, voided)"
               " VALUES ('2026-10-19', 'shoulder', 'right', 2, 'manual', 1)")
    con.commit()
    con.close()
    r = db_client.get("/api/log/pain-recent")
    items = r.get_json()["items"]
    assert len(items) == 1 and items[0]["region"] == "low-back"  # voided row excluded
