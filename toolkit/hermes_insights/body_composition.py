"""Multi-frequency BIA "body composition analysis report" (Relaxmedic-style):
validated capture and read-model for Migration 008's four additive tables.

docs/PRODUCT_AUTHORITY.md §"Fork deployment: body composition analysis
report" records the owner decision. One JSON document on stdin describes one
dated scan: scalar device/subject header + obesity/score/control fields, a
closed set of weight-component breakdowns, a five-segment fat/muscle balance,
and a frequency x segment impedance matrix. Re-ingesting the same
(date, tested_at, device) replaces that scan's children — never duplicates.
"""
import math

from . import migrations, runtime
from .body_contracts import BODY_COMPOSITION_CLAMPS, BODY_CLAMPS
from .calculations import _hhmm_min
from .importers.common import valid_date

COMPONENTS = (
    "weight", "body_fat", "inorganic_salt", "protein",
    "body_water", "muscle", "skeletal_muscle",
)
SEGMENTS = ("left_arm", "right_arm", "trunk", "left_leg", "right_leg")
SEGMENT_KINDS = ("fat", "muscle")
EVALUATIONS = ("Low", "Standard", "High")
SEX_VALUES = ("male", "female", "other")
BMI_BANDS = ("Thin", "Standard", "High", "Too High")
OBESITY_DEGREE_BANDS = ("Low", "Normal", "High")
BODY_TYPES = (
    "Athletes", "Slightly obese", "Muscle", "Overweight", "Muscular Slim",
    "Slim", "Marasmus", "Thin", "Invisible obesity", "Obesity",
)

MAX_NOTES = 2000
MAX_REPORT_REF = 300
MAX_LABEL = 160

_SCAN_SCALAR_FIELDS = (
    # (json key, column, clamp key, integer?)
    ("bmi", "bmi", "bmi", False),
    ("body_fat_pct", "body_fat_pct", "body_fat_pct", False),
    ("body_score", "body_score", "body_score", True),
    ("target_weight_kg", "target_weight_kg", "target_weight_kg", False),
    ("weight_control_kg", "weight_control_kg", "weight_control_kg", False),
    ("fat_control_kg", "fat_control_kg", "fat_control_kg", False),
    ("muscle_control_kg", "muscle_control_kg", "muscle_control_kg", False),
    ("obesity_degree_pct", "obesity_degree_pct", "obesity_degree_pct", False),
    ("visceral_fat_grade", "visceral_fat_grade", "visceral_fat_grade", True),
    ("bmr_kcal", "bmr_kcal", "bmr_kcal", True),
    ("fat_free_mass_kg", "fat_free_mass_kg", "fat_free_mass_kg", False),
    ("subcutaneous_fat_pct", "subcutaneous_fat_pct", "subcutaneous_fat_pct", False),
    ("smi_kg_m2", "smi_kg_m2", "smi_kg_m2", False),
    ("body_age", "body_age", "body_age", True),
    ("whr", "whr", "whr", False),
)
_SCAN_BAND_FIELDS = (
    ("bmi_band", BMI_BANDS),
    ("body_fat_band", BMI_BANDS),
    ("obesity_degree_band", OBESITY_DEGREE_BANDS),
    ("body_type", BODY_TYPES),
)


def _err(message):
    raise SystemExit(message)


def _require_dict(value, what):
    if not isinstance(value, dict):
        _err(f"{what} must be an object")
    return value


def _number(value, key, clamp_key, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _err(f"{key} must be a number")
    v = float(value)
    if not math.isfinite(v):
        _err(f"{key} must be finite")
    if integer and v != int(v):
        _err(f"{key} must be a whole number")
    lo, hi = BODY_COMPOSITION_CLAMPS[clamp_key]
    if not lo <= v <= hi:
        _err(f"{key} out of range [{lo}, {hi}]")
    return int(v) if integer else v


def _optional_number(data, key, clamp_key, *, integer=False):
    if key not in data or data[key] is None:
        return None
    return _number(data[key], key, clamp_key, integer=integer)


def _optional_text(data, key, maximum):
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        _err(f"{key} must be a string")
    value = value.strip()
    if not value:
        return None
    if len(value) > maximum or "\x00" in value:
        _err(f"{key} must be at most {maximum} characters without NUL")
    return value


def _optional_enum(data, key, allowed):
    value = data.get(key)
    if value is None:
        return None
    if value not in allowed:
        _err(f"{key} must be one of: {', '.join(allowed)}")
    return value


def _validate_components(items):
    if items is None:
        return []
    if not isinstance(items, list):
        _err("components must be an array")
    seen = set()
    rows = []
    for item in items:
        item = _require_dict(item, "each components entry")
        extra = set(item) - {"component", "value_kg", "ref_low", "ref_high",
                             "pct_of_weight", "evaluation"}
        if extra:
            _err(f"unknown components field(s): {', '.join(sorted(extra))}")
        component = item.get("component")
        if component not in COMPONENTS:
            _err(f"component must be one of: {', '.join(COMPONENTS)}")
        if component in seen:
            _err(f"duplicate component: {component}")
        seen.add(component)
        if "value_kg" not in item:
            _err(f"{component}: value_kg is required")
        value_kg = _number(item["value_kg"], f"{component}.value_kg", "component_value_kg")
        ref_low = _optional_number(item, "ref_low", "component_ref_kg")
        ref_high = _optional_number(item, "ref_high", "component_ref_kg")
        if ref_low is not None and ref_high is not None and ref_low > ref_high:
            _err(f"{component}: ref_low must be <= ref_high")
        pct_of_weight = _optional_number(item, "pct_of_weight", "component_pct_of_weight")
        evaluation = _optional_enum(item, "evaluation", EVALUATIONS)
        rows.append({
            "component": component, "value_kg": value_kg, "ref_low": ref_low,
            "ref_high": ref_high, "pct_of_weight": pct_of_weight,
            "evaluation": evaluation,
        })
    return rows


def _validate_segments(items):
    if items is None:
        return []
    if not isinstance(items, list):
        _err("segments must be an array")
    seen = set()
    rows = []
    for item in items:
        item = _require_dict(item, "each segments entry")
        extra = set(item) - {"kind", "segment", "mass_kg", "pct_of_standard",
                             "evaluation", "std_low_pct", "std_high_pct"}
        if extra:
            _err(f"unknown segments field(s): {', '.join(sorted(extra))}")
        kind = item.get("kind")
        if kind not in SEGMENT_KINDS:
            _err(f"kind must be one of: {', '.join(SEGMENT_KINDS)}")
        segment = item.get("segment")
        if segment not in SEGMENTS:
            _err(f"segment must be one of: {', '.join(SEGMENTS)}")
        key = (kind, segment)
        if key in seen:
            _err(f"duplicate segment: {kind}/{segment}")
        seen.add(key)
        if "mass_kg" not in item:
            _err(f"{kind}/{segment}: mass_kg is required")
        mass_kg = _number(item["mass_kg"], f"{kind}.{segment}.mass_kg", "segment_mass_kg")
        pct_of_standard = _optional_number(item, "pct_of_standard", "segment_pct_of_standard")
        evaluation = _optional_enum(item, "evaluation", EVALUATIONS)
        std_low_pct = _optional_number(item, "std_low_pct", "segment_std_pct")
        std_high_pct = _optional_number(item, "std_high_pct", "segment_std_pct")
        if std_low_pct is not None and std_high_pct is not None and std_low_pct > std_high_pct:
            _err(f"{kind}/{segment}: std_low_pct must be <= std_high_pct")
        rows.append({
            "kind": kind, "segment": segment, "mass_kg": mass_kg,
            "pct_of_standard": pct_of_standard, "evaluation": evaluation,
            "std_low_pct": std_low_pct, "std_high_pct": std_high_pct,
        })
    return rows


def _validate_impedance(items):
    if items is None:
        return []
    if not isinstance(items, list):
        _err("impedance must be an array")
    seen = set()
    rows = []
    for item in items:
        item = _require_dict(item, "each impedance entry")
        extra = set(item) - {"frequency_khz", "segment", "ohms"}
        if extra:
            _err(f"unknown impedance field(s): {', '.join(sorted(extra))}")
        if "frequency_khz" not in item:
            _err("impedance entry requires frequency_khz")
        frequency_khz = _number(item["frequency_khz"], "frequency_khz", "impedance_khz")
        segment = item.get("segment")
        if segment not in SEGMENTS:
            _err(f"segment must be one of: {', '.join(SEGMENTS)}")
        key = (frequency_khz, segment)
        if key in seen:
            _err(f"duplicate impedance row: {frequency_khz}kHz/{segment}")
        seen.add(key)
        if "ohms" not in item:
            _err(f"{frequency_khz}kHz/{segment}: ohms is required")
        ohms = _number(item["ohms"], f"{frequency_khz}kHz.{segment}.ohms", "impedance_ohms")
        rows.append({"frequency_khz": frequency_khz, "segment": segment, "ohms": ohms})
    return rows


_TOP_LEVEL_FIELDS = {
    "date", "tested_at", "device", "subject_label", "sex", "age_years",
    "height_cm", "components", "segments", "impedance", "notes",
    "report_ref", "source",
} | {key for key, *_ in _SCAN_SCALAR_FIELDS} | {key for key, _ in _SCAN_BAND_FIELDS}

CAPTURE_SOURCES = {"manual", "ui", "chat", "chat-panel", "chat-telegram", "panel-ui"}


def validate_report(data):
    """Validate one closed-schema BIA report document; return its normalized
    fields (never touches the database). Raises SystemExit on any violation."""
    data = _require_dict(data, "body composition report")
    extra = set(data) - _TOP_LEVEL_FIELDS
    if extra:
        _err(f"unknown field(s): {', '.join(sorted(extra))}")
    if "date" not in data:
        _err("date is required")
    report = {"date": valid_date(data["date"])}

    tested_at = data.get("tested_at")
    if tested_at is None:
        report["tested_at"] = ""
    else:
        if not isinstance(tested_at, str) or _hhmm_min(tested_at) is None:
            _err("tested_at must be HH:MM")
        report["tested_at"] = tested_at

    report["device"] = _optional_text(data, "device", MAX_LABEL) or ""
    report["subject_label"] = _optional_text(data, "subject_label", MAX_LABEL)
    report["sex"] = _optional_enum(data, "sex", SEX_VALUES)
    report["age_years"] = _optional_number(data, "age_years", "age_years", integer=True)
    report["height_cm"] = _optional_number(data, "height_cm", "height_cm")
    report["notes"] = _optional_text(data, "notes", MAX_NOTES)
    report["report_ref"] = _optional_text(data, "report_ref", MAX_REPORT_REF)
    source = data.get("source")
    if source is not None and source not in CAPTURE_SOURCES:
        _err(f"source must be one of: {', '.join(sorted(CAPTURE_SOURCES))}")
    report["source"] = source or "manual"

    for key, _column, clamp_key, integer in _SCAN_SCALAR_FIELDS:
        report[key] = _optional_number(data, key, clamp_key, integer=integer)
    for key, allowed in _SCAN_BAND_FIELDS:
        report[key] = _optional_enum(data, key, allowed)

    report["components"] = _validate_components(data.get("components"))
    report["segments"] = _validate_segments(data.get("segments"))
    report["impedance"] = _validate_impedance(data.get("impedance"))
    return report


_SCAN_COLUMNS = (
    "date", "tested_at", "device", "subject_label", "sex", "age_years",
    "height_cm", "bmi", "body_fat_pct", "body_score", "target_weight_kg",
    "weight_control_kg", "fat_control_kg", "muscle_control_kg", "bmi_band",
    "body_fat_band", "obesity_degree_pct", "obesity_degree_band",
    "body_type", "visceral_fat_grade", "bmr_kcal", "fat_free_mass_kg",
    "subcutaneous_fat_pct", "smi_kg_m2", "body_age", "whr", "notes",
    "report_ref", "source",
)


def _require_tables(c):
    migrations.require_table(c, "body_composition_scans")
    migrations.require_table(c, "body_composition_components")
    migrations.require_table(c, "body_composition_segments")
    migrations.require_table(c, "body_composition_impedance")


def _mirror_body_metrics(c, report):
    """Idempotently mirror weight/body-fat% into body_metrics (source
    'bia-scan') so existing trends pick up a scan without a second manual
    entry. Re-ingesting the same date replaces only this writer's own
    'bia-scan' row — a 'manual' row for the same date is a separate row and
    is never touched (the read-side tie-break in app/routes/dash.py keeps a
    manual entry ranked ahead of a bia-scan one)."""
    weight_kg = next(
        (row["value_kg"] for row in report["components"] if row["component"] == "weight"),
        None,
    )
    body_fat_pct = report.get("body_fat_pct")
    if weight_kg is None and body_fat_pct is None:
        return
    if weight_kg is not None:
        lo, hi = BODY_CLAMPS["weight_kg"]
        if not lo <= weight_kg <= hi:
            return
    c.execute(
        "DELETE FROM body_metrics WHERE date=? AND source='bia-scan'",
        (report["date"],),
    )
    c.execute(
        "INSERT INTO body_metrics(date, weight_kg, body_fat_pct, source) "
        "VALUES(?,?,?,'bia-scan')",
        (report["date"], weight_kg, body_fat_pct),
    )


def log_body_composition(c, report):
    """Write one validated report (already normalized by validate_report).
    Idempotent upsert keyed on (date, tested_at, device): a prior scan with
    the same key is deleted (cascading its components/segments/impedance)
    before the replacement is inserted, in the same transaction."""
    _require_tables(c)
    existing = c.execute(
        "SELECT id FROM body_composition_scans WHERE date=? AND tested_at=? AND device=?",
        (report["date"], report["tested_at"], report["device"]),
    ).fetchone()
    if existing is not None:
        c.execute("DELETE FROM body_composition_scans WHERE id=?", (existing["id"],))
    columns = ",".join(_SCAN_COLUMNS)
    placeholders = ",".join("?" * len(_SCAN_COLUMNS))
    c.execute(
        f"INSERT INTO body_composition_scans({columns}) VALUES({placeholders})",
        tuple(report[key] for key in _SCAN_COLUMNS),
    )
    scan_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    for row in report["components"]:
        c.execute(
            "INSERT INTO body_composition_components"
            "(scan_id, component, value_kg, ref_low, ref_high, pct_of_weight, evaluation) "
            "VALUES(?,?,?,?,?,?,?)",
            (scan_id, row["component"], row["value_kg"], row["ref_low"],
             row["ref_high"], row["pct_of_weight"], row["evaluation"]),
        )
    for row in report["segments"]:
        c.execute(
            "INSERT INTO body_composition_segments"
            "(scan_id, kind, segment, mass_kg, pct_of_standard, evaluation, std_low_pct, std_high_pct) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (scan_id, row["kind"], row["segment"], row["mass_kg"],
             row["pct_of_standard"], row["evaluation"], row["std_low_pct"],
             row["std_high_pct"]),
        )
    for row in report["impedance"]:
        c.execute(
            "INSERT INTO body_composition_impedance(scan_id, frequency_khz, segment, ohms) "
            "VALUES(?,?,?,?)",
            (scan_id, row["frequency_khz"], row["segment"], row["ohms"]),
        )
    _mirror_body_metrics(c, report)
    return {"ok": True, "scan_id": scan_id, "date": report["date"],
            "replaced": existing is not None}


def _scan_row(c, row):
    scan = dict(row)
    scan_id = scan["id"]
    scan["components"] = [
        dict(r) for r in c.execute(
            "SELECT component, value_kg, ref_low, ref_high, pct_of_weight, evaluation "
            "FROM body_composition_components WHERE scan_id=? ORDER BY component", (scan_id,))
    ]
    scan["segments"] = {
        kind: [
            dict(r) for r in c.execute(
                "SELECT segment, mass_kg, pct_of_standard, evaluation, std_low_pct, std_high_pct "
                "FROM body_composition_segments WHERE scan_id=? AND kind=? ORDER BY segment",
                (scan_id, kind))
        ] for kind in SEGMENT_KINDS
    }
    scan["impedance"] = [
        dict(r) for r in c.execute(
            "SELECT frequency_khz, segment, ohms FROM body_composition_impedance "
            "WHERE scan_id=? ORDER BY frequency_khz, segment", (scan_id,))
    ]
    return scan


def body_composition(c, *, days, latest, clock):
    """Latest scan fully nested, plus (unless `latest`) a trend series over
    the trailing `days` window. Honest insufficient_data when there is no
    scan at all."""
    _require_tables(c)
    latest_row = c.execute(
        "SELECT * FROM body_composition_scans ORDER BY date DESC, tested_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if latest_row is None:
        return {"ok": True, "insufficient_data": True,
                "reason": "no body composition scan has been logged yet"}
    result = {"ok": True, "latest": _scan_row(c, latest_row)}
    if latest:
        return result
    lo = runtime.days_ago(max(int(days), 1), clock=clock)
    rows = c.execute(
        """SELECT s.date, s.body_score, s.visceral_fat_grade, s.bmr_kcal, s.smi_kg_m2,
                  s.body_fat_pct,
                  (SELECT value_kg FROM body_composition_components
                     WHERE scan_id=s.id AND component='weight') AS weight_kg,
                  (SELECT value_kg FROM body_composition_components
                     WHERE scan_id=s.id AND component='body_fat') AS fat_mass_kg,
                  (SELECT value_kg FROM body_composition_components
                     WHERE scan_id=s.id AND component='skeletal_muscle') AS skeletal_muscle_kg
           FROM body_composition_scans s
           WHERE s.date >= ?
           ORDER BY s.date, s.tested_at, s.id""",
        (lo,),
    ).fetchall()
    result["days"] = int(days)
    result["trend"] = [dict(r) for r in rows]
    return result
