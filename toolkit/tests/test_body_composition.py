"""§body-composition: Migration 008 (multi-frequency BIA report) and the
validated log-body-composition / body-composition commands.

Fictional data only. Numbers below are invented, not the real report the
fork owner described when authorizing this feature (docs/PRODUCT_AUTHORITY.md
"Fork deployment: body composition analysis report").
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text(encoding="utf-8")
CODE_VERSION = "8" * 40

sys.path.insert(0, str(ROOT))
import hermes_insights.migrations as migrations  # noqa: E402


def make_db(tmp_path, name="health.db"):
    p = tmp_path / name
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


@pytest.fixture()
def db(tmp_path):
    return make_db(tmp_path)


def run(db, *args, expect_ok=True, stdin=None):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       input=stdin, capture_output=True, text=True, timeout=30)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def jout(r):
    return json.loads(r.stdout)


def rows(db, sql, params=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def _full_report(**overrides):
    """A fictional, complete report — every storable field populated with
    invented numbers in the report's shape, not the owner's real values."""
    report = {
        "date": "2026-02-10", "tested_at": "08:15", "device": "FictionalScale 9000",
        "subject_label": "Demo User", "sex": "male", "age_years": 41, "height_cm": 181,
        "components": [
            {"component": "weight", "value_kg": 84.4, "ref_low": 62.0, "ref_high": 83.0,
             "pct_of_weight": 100.0, "evaluation": "High"},
            {"component": "body_fat", "value_kg": 21.1, "ref_low": 9.0, "ref_high": 18.0,
             "pct_of_weight": 25.0, "evaluation": "High"},
            {"component": "inorganic_salt", "value_kg": 3.7, "ref_low": 3.1, "ref_high": 4.0,
             "pct_of_weight": 4.4, "evaluation": "Standard"},
            {"component": "protein", "value_kg": 11.2, "ref_low": 9.5, "ref_high": 11.8,
             "pct_of_weight": 13.3, "evaluation": "Standard"},
            {"component": "body_water", "value_kg": 40.9, "ref_low": 35.0, "ref_high": 43.0,
             "pct_of_weight": 48.5, "evaluation": "Standard"},
            {"component": "muscle", "value_kg": 51.1, "ref_low": 44.0, "ref_high": 55.0,
             "pct_of_weight": 60.5, "evaluation": "Standard"},
            {"component": "skeletal_muscle", "value_kg": 31.0, "ref_low": 27.5, "ref_high": 33.5,
             "pct_of_weight": 36.7, "evaluation": "Standard"},
        ],
        "segments": [
            {"kind": "fat", "segment": "left_arm", "mass_kg": 1.8, "pct_of_standard": 310.0,
             "evaluation": "High", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "fat", "segment": "right_arm", "mass_kg": 1.9, "pct_of_standard": 315.0,
             "evaluation": "High", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "fat", "segment": "trunk", "mass_kg": 12.1, "pct_of_standard": 340.0,
             "evaluation": "High", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "fat", "segment": "left_leg", "mass_kg": 3.4, "pct_of_standard": 250.0,
             "evaluation": "High", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "fat", "segment": "right_leg", "mass_kg": 3.3, "pct_of_standard": 248.0,
             "evaluation": "High", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "muscle", "segment": "left_arm", "mass_kg": 3.0, "pct_of_standard": 95.0,
             "evaluation": "Standard", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "muscle", "segment": "right_arm", "mass_kg": 3.1, "pct_of_standard": 96.0,
             "evaluation": "Standard", "std_low_pct": 80, "std_high_pct": 160},
            {"kind": "muscle", "segment": "trunk", "mass_kg": 23.5, "pct_of_standard": 92.0,
             "evaluation": "Standard", "std_low_pct": 90, "std_high_pct": 110},
            {"kind": "muscle", "segment": "left_leg", "mass_kg": 9.0, "pct_of_standard": 98.0,
             "evaluation": "Standard", "std_low_pct": 90, "std_high_pct": 110},
            {"kind": "muscle", "segment": "right_leg", "mass_kg": 9.1, "pct_of_standard": 99.0,
             "evaluation": "Standard", "std_low_pct": 90, "std_high_pct": 110},
        ],
        "impedance": [
            {"frequency_khz": 20, "segment": "right_arm", "ohms": 320.1},
            {"frequency_khz": 20, "segment": "left_arm", "ohms": 330.4},
            {"frequency_khz": 20, "segment": "trunk", "ohms": 24.5},
            {"frequency_khz": 20, "segment": "right_leg", "ohms": 260.2},
            {"frequency_khz": 20, "segment": "left_leg", "ohms": 245.9},
            {"frequency_khz": 100, "segment": "right_arm", "ohms": 285.3},
            {"frequency_khz": 100, "segment": "left_arm", "ohms": 294.7},
            {"frequency_khz": 100, "segment": "trunk", "ohms": 23.1},
            {"frequency_khz": 100, "segment": "right_leg", "ohms": 228.4},
            {"frequency_khz": 100, "segment": "left_leg", "ohms": 214.0},
        ],
        "bmi": 25.8, "body_fat_pct": 25.0, "body_score": 64,
        "target_weight_kg": 76.0, "weight_control_kg": -8.4, "fat_control_kg": -8.4,
        "muscle_control_kg": 0.0, "bmi_band": "High", "body_fat_band": "High",
        "obesity_degree_pct": 111, "obesity_degree_band": "Normal", "body_type": "Overweight",
        "visceral_fat_grade": 9, "bmr_kcal": 1702, "fat_free_mass_kg": 63.3,
        "subcutaneous_fat_pct": 19.2, "smi_kg_m2": 9.1, "body_age": 38, "whr": 0.92,
        "notes": "fictional note", "report_ref": "raw/labs/fictional-bia-2026-02-10.pdf",
        "source": "manual",
    }
    report.update(overrides)
    return report


# ── Migration 008 ────────────────────────────────────────────────────────────

def _strip_body_composition_tables(schema: str) -> str:
    start = schema.index("-- Multi-frequency BIA")
    end = schema.index("CREATE TABLE body_metrics")
    return schema[:start] + schema[end:]


def test_migration_7_to_8_adds_body_composition_tables_on_a_legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(_strip_body_composition_tables(SCHEMA))
    con.commit()
    con.close()

    at_v7 = migrations.migrate(str(path), 7, 0, CODE_VERSION)
    assert at_v7["to_version"] == 7
    for table in ("body_composition_scans", "body_composition_components",
                  "body_composition_segments", "body_composition_impedance"):
        assert sqlite3.connect(path).execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None

    upgraded = migrations.migrate(str(path), 8, 7, CODE_VERSION)
    assert upgraded["to_version"] == 8
    assert [item["version"] for item in upgraded["applied"]] == [8]
    for table in ("body_composition_scans", "body_composition_components",
                  "body_composition_segments", "body_composition_impedance"):
        assert sqlite3.connect(path).execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    status = migrations.schema_status(str(path))
    assert status["current_version"] == 8 and status["status"] == "up_to_date"


def test_clean_install_lands_at_v8_with_body_composition_tables(tmp_path):
    path = tmp_path / "fresh.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    result = migrations.migrate(str(path), migrations.AUTONOMOUS_SCHEMA_VERSION, 0, CODE_VERSION)
    assert result["to_version"] == 8
    assert migrations.AUTONOMOUS_SCHEMA_VERSION == 8


# ── log-body-composition: full round trip ───────────────────────────────────

def test_full_report_round_trips_every_field(db):
    report = _full_report()
    out = jout(run(db, "log-body-composition", stdin=json.dumps(report)))
    assert out["ok"] is True and out["replaced"] is False

    scan = rows(db, "SELECT * FROM body_composition_scans")
    assert len(scan) == 1
    scan = scan[0]
    for key in ("date", "tested_at", "device", "subject_label", "sex", "age_years",
                "height_cm", "bmi", "body_fat_pct", "body_score", "target_weight_kg",
                "weight_control_kg", "fat_control_kg", "muscle_control_kg", "bmi_band",
                "body_fat_band", "obesity_degree_pct", "obesity_degree_band", "body_type",
                "visceral_fat_grade", "bmr_kcal", "fat_free_mass_kg",
                "subcutaneous_fat_pct", "smi_kg_m2", "body_age", "whr", "notes",
                "report_ref", "source"):
        assert scan[key] == report[key], key

    components = rows(db, "SELECT * FROM body_composition_components ORDER BY component")
    assert len(components) == 7
    by_name = {c["component"]: c for c in components}
    for item in report["components"]:
        got = by_name[item["component"]]
        assert got["value_kg"] == item["value_kg"]
        assert got["ref_low"] == item["ref_low"]
        assert got["ref_high"] == item["ref_high"]
        assert got["pct_of_weight"] == item["pct_of_weight"]
        assert got["evaluation"] == item["evaluation"]

    segments = rows(db, "SELECT * FROM body_composition_segments")
    assert len(segments) == 10
    by_key = {(s["kind"], s["segment"]): s for s in segments}
    for item in report["segments"]:
        got = by_key[(item["kind"], item["segment"])]
        assert got["mass_kg"] == item["mass_kg"]
        assert got["pct_of_standard"] == item["pct_of_standard"]
        assert got["evaluation"] == item["evaluation"]
        assert got["std_low_pct"] == item["std_low_pct"]
        assert got["std_high_pct"] == item["std_high_pct"]

    impedance = rows(db, "SELECT * FROM body_composition_impedance")
    assert len(impedance) == 10
    by_freq_seg = {(i["frequency_khz"], i["segment"]): i for i in impedance}
    for item in report["impedance"]:
        got = by_freq_seg[(float(item["frequency_khz"]), item["segment"])]
        assert got["ohms"] == item["ohms"]


def test_reingest_same_key_is_idempotent_not_duplicated(db):
    report = _full_report()
    first = jout(run(db, "log-body-composition", stdin=json.dumps(report)))
    second = jout(run(db, "log-body-composition", stdin=json.dumps(report)))
    assert first["replaced"] is False
    assert second["replaced"] is True
    assert second["scan_id"] != first["scan_id"]
    assert len(rows(db, "SELECT id FROM body_composition_scans")) == 1
    assert len(rows(db, "SELECT id FROM body_composition_components")) == 7
    assert len(rows(db, "SELECT id FROM body_composition_segments")) == 10
    assert len(rows(db, "SELECT id FROM body_composition_impedance")) == 10


def test_different_tested_at_or_device_creates_a_separate_scan(db):
    report = _full_report()
    run(db, "log-body-composition", stdin=json.dumps(report))
    other_time = _full_report(tested_at="18:00")
    run(db, "log-body-composition", stdin=json.dumps(other_time))
    other_device = _full_report(device="OtherScale")
    run(db, "log-body-composition", stdin=json.dumps(other_device))
    assert len(rows(db, "SELECT id FROM body_composition_scans")) == 3


# ── validation ───────────────────────────────────────────────────────────────

def test_rejects_unknown_top_level_key(db):
    report = _full_report()
    report["bogus"] = 1
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "unknown field" in r.stderr


def test_rejects_unknown_component_field(db):
    report = _full_report()
    report["components"][0]["bogus"] = 1
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "unknown components field" in r.stderr


def test_rejects_bad_component_enum(db):
    report = _full_report()
    report["components"][0]["component"] = "fat_mass"
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "component must be one of" in r.stderr


def test_rejects_duplicate_component(db):
    report = _full_report()
    report["components"].append(dict(report["components"][0]))
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "duplicate component" in r.stderr


def test_rejects_bad_segment_kind_and_segment_name(db):
    report = _full_report()
    report["segments"][0]["kind"] = "lean"
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "kind must be one of" in r.stderr

    report2 = _full_report()
    report2["segments"][0]["segment"] = "torso"
    r2 = run(db, "log-body-composition", stdin=json.dumps(report2), expect_ok=False)
    assert r2.returncode != 0 and "segment must be one of" in r2.stderr


def test_rejects_bad_evaluation_enum(db):
    report = _full_report()
    report["components"][0]["evaluation"] = "Too High"
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "evaluation must be one of" in r.stderr


def test_rejects_ref_low_above_ref_high(db):
    report = _full_report()
    report["components"][0]["ref_low"] = 90
    report["components"][0]["ref_high"] = 50
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "ref_low must be <= ref_high" in r.stderr


def test_rejects_std_low_above_std_high(db):
    report = _full_report()
    report["segments"][0]["std_low_pct"] = 200
    report["segments"][0]["std_high_pct"] = 50
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "std_low_pct must be <= std_high_pct" in r.stderr


def test_rejects_out_of_range_numeric(db):
    report = _full_report()
    report["bmi"] = 500
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "out of range" in r.stderr


def test_rejects_bad_date(db):
    report = _full_report()
    report["date"] = "10-02-2026"
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "--date" in r.stderr


def test_rejects_missing_date(db):
    report = _full_report()
    del report["date"]
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "date is required" in r.stderr


def test_rejects_duplicate_impedance_row(db):
    report = _full_report()
    report["impedance"].append(dict(report["impedance"][0]))
    r = run(db, "log-body-composition", stdin=json.dumps(report), expect_ok=False)
    assert r.returncode != 0 and "duplicate impedance row" in r.stderr


def test_rejects_non_object_document(db):
    r = run(db, "log-body-composition", stdin=json.dumps([1, 2, 3]), expect_ok=False)
    assert r.returncode != 0 and "must be an object" in r.stderr


def test_rejects_malformed_json(db):
    r = run(db, "log-body-composition", stdin="{not json", expect_ok=False)
    assert r.returncode != 0 and "not valid JSON" in r.stderr


def test_minimal_report_with_only_date_is_accepted(db):
    out = jout(run(db, "log-body-composition", stdin=json.dumps({"date": "2026-03-01"})))
    assert out["ok"] is True
    scan = rows(db, "SELECT * FROM body_composition_scans")[0]
    assert scan["device"] == "" and scan["tested_at"] == "" and scan["source"] == "manual"
    assert scan["bmi"] is None


# ── body_metrics mirror + manual precedence ─────────────────────────────────

def test_mirrors_weight_and_body_fat_into_body_metrics(db):
    report = _full_report()
    run(db, "log-body-composition", stdin=json.dumps(report))
    mirrored = rows(db, "SELECT * FROM body_metrics WHERE source='bia-scan'")
    assert len(mirrored) == 1
    assert mirrored[0]["date"] == report["date"]
    assert mirrored[0]["weight_kg"] == 84.4
    assert mirrored[0]["body_fat_pct"] == 25.0


def test_reingest_replaces_only_the_bia_scan_mirror_row(db):
    report = _full_report()
    run(db, "log-body-composition", stdin=json.dumps(report))
    other = _full_report()
    other["components"] = [dict(c) for c in report["components"]]
    for c in other["components"]:
        if c["component"] == "weight":
            c["value_kg"] = 80.0
    run(db, "log-body-composition", stdin=json.dumps(other))
    mirrored = rows(db, "SELECT * FROM body_metrics WHERE source='bia-scan'")
    assert len(mirrored) == 1
    assert mirrored[0]["weight_kg"] == 80.0


def test_manual_body_metrics_row_is_never_touched_by_the_scan_writer(db):
    run(db, "log", "body_metrics", "weight_kg=79.5", f"date={_full_report()['date']}")
    report = _full_report()
    run(db, "log-body-composition", stdin=json.dumps(report))
    manual = rows(db, "SELECT * FROM body_metrics WHERE source='manual'")
    bia = rows(db, "SELECT * FROM body_metrics WHERE source='bia-scan'")
    assert len(manual) == 1 and manual[0]["weight_kg"] == 79.5
    assert len(bia) == 1 and bia[0]["weight_kg"] == 84.4


# ── read: latest / trend / insufficient_data ────────────────────────────────

def test_read_insufficient_data_when_empty(db):
    out = jout(run(db, "body-composition"))
    assert out["insufficient_data"] is True


def test_read_latest_returns_fully_nested_scan(db):
    report = _full_report()
    run(db, "log-body-composition", stdin=json.dumps(report))
    out = jout(run(db, "body-composition", "--latest"))
    assert "trend" not in out
    latest = out["latest"]
    assert latest["date"] == report["date"]
    assert len(latest["components"]) == 7
    assert len(latest["segments"]["fat"]) == 5
    assert len(latest["segments"]["muscle"]) == 5
    assert len(latest["impedance"]) == 10


# ── multi-user isolation ─────────────────────────────────────────────────────

def test_user_a_scan_is_invisible_in_user_bs_database(tmp_path):
    """Isolation is per-database (one health.db per user; see docs/MULTI_USER.md
    and app.users.user_health_db) — this is the actual mechanism the panel,
    bridge and MCP all rely on. Prove it directly: a scan logged against
    user A's own health.db never appears when the SAME command runs against
    user B's own, separate health.db."""
    db_a = make_db(tmp_path, "user-a-health.db")
    db_b = make_db(tmp_path, "user-b-health.db")
    run(db_a, "log-body-composition", stdin=json.dumps(_full_report(bmi=22.0)))

    out_a = jout(run(db_a, "body-composition", "--latest"))
    out_b = jout(run(db_b, "body-composition", "--latest"))
    assert out_a["latest"]["bmi"] == 22.0
    assert out_b["insufficient_data"] is True
    assert "22.0" not in json.dumps(out_b)
    assert len(rows(db_b, "SELECT id FROM body_composition_scans")) == 0


def test_read_trend_window_and_latest_pick_newest(db):
    # The trend window is relative to the REAL clock (runtime.days_ago), not
    # the scan date — anchor both fictional dates off today so a 30-day
    # window predictably keeps one and excludes the other.
    from datetime import date, timedelta
    today = date.today()
    old_date = (today - timedelta(days=500)).isoformat()
    new_date = (today - timedelta(days=1)).isoformat()
    old = _full_report(date=old_date)
    new = _full_report(date=new_date)
    run(db, "log-body-composition", stdin=json.dumps(old))
    run(db, "log-body-composition", stdin=json.dumps(new))
    out = jout(run(db, "body-composition", "--days", "30"))
    assert out["latest"]["date"] == new_date
    dates = [t["date"] for t in out["trend"]]
    assert old_date not in dates
    assert new_date in dates
