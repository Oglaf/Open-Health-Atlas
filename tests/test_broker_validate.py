"""Broker-side _validate/_audit_argv unit tests (loaded from the script file).
The full broker is drilled live on the server; these pin the flag-parsing and
audit-redaction rules that reviews flagged."""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import pytest
import sys
import threading
from unittest.mock import patch

from app import bridge as client_bridge

BROKER = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "hermes-bridge"


def _load():
    loader = importlib.machinery.SourceFileLoader("broker", str(BROKER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    # Direct script execution puts adjacent installed modules on sys.path.
    with patch.object(sys, "path", [str(BROKER.parent), *sys.path]):
        loader.exec_module(mod)
    return mod


def test_flag_value_starting_with_dash_is_data():
    b = _load()
    assert b._validate("log-commitment", ["kept", "--why", "-felt tired"]) is None


def test_unknown_flag_still_refused():
    b = _load()
    assert b._validate("log-commitment", ["kept", "--nope", "x"]) is not None


def test_recipe_import_and_batch_portion_changes_remain_unavailable():
    b = _load()
    assert b._validate("import-recipes", ["recipes.csv"]) is not None
    assert b._validate("set-batch", ["sample-stew", "--grams", "500"]) is None
    assert b._validate(
        "set-batch", ["sample-stew", "--grams", "500", "--portions", "2"]
    ) is not None


def test_green_day_readonly_profile_exposes_only_governed_analysis(monkeypatch):
    monkeypatch.setenv("BRIDGE_PROFILE", "dashboard-green-days-readonly")
    b = _load()
    assert set(b.ALLOWED) == {"outcome-associations", "analysis-job-start", "analysis-job-status"}
    assert b._validate(
        "outcome-associations",
        [
            "--outcome", "subjective.day_rating",
            "--mode", "green-vs-non-green",
            "--from", "2026-05-17", "--to", "2026-06-30",
            "--min-n", "30", "--interactions", "none", "--top", "3",
        ],
    ) is None
    assert b._validate("day-rating", ["green"]) is not None
    assert b._validate("finding-evidence", ["--all"]) is not None


def test_analysis_jobs_reject_execution_paths_and_closed_request_bypasses():
    b = _load()
    request = {"command": "outcome-associations", "args": ["--outcome", "subjective.day_rating", "--all"]}
    assert b._validate("analysis-job-start", []) is None
    assert b._analysis_request(json.dumps(request)) is None
    assert b._validate("analysis-job-status", ["a" * 32]) is None
    assert b._validate("analysis-job-status", ["../private"]) is not None
    assert b._validate("analysis-job-work", []) is not None
    assert b._validate("analysis-job-execute", ["a" * 32, "b" * 32]) is not None
    for invalid in (
        {**request, "path": "/tmp/other.db"},
        {**request, "command": "query"},
        {**request, "args": ["--outcome", "x", "--outcome", "y"]},
        {**request, "args": ["--outcome=x"]},
        {**request, "args": ["--database", "/tmp/other.db"]},
        {**request, "args": ["--outcome", "--all"]},
    ):
        assert b._analysis_request(json.dumps(invalid)) is not None


def test_analysis_worker_drains_then_sleeps_without_losing_late_submission(monkeypatch):
    b = _load()
    stop, wake = threading.Event(), threading.Event()
    monkeypatch.setattr(b, "_JOB_STOP", stop)
    monkeypatch.setattr(b, "_JOB_WAKE", wake)
    monkeypatch.setattr(b.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(b, "_audit", lambda _: None)
    # Three queued requests drain despite one coalesced wake. A submission
    # arriving after the first empty check must trigger another immediate read.
    outcomes = iter([True, True, True, False, True, False])
    launches, waits = [], []

    class Child:
        returncode = 0

        def __init__(self, *args, **kwargs):
            processed = next(outcomes)
            launches.append(processed)
            self.stdout = io.BytesIO(json.dumps({"ok": True, "processed": processed}).encode())
            if len(launches) == 4:
                wake.set()  # durable submit raced with this empty queue result

        def poll(self):
            return 0

    def wait(timeout):
        waits.append((timeout, wake.is_set()))
        if len(waits) == 1:
            assert wake.is_set()
            return True
        stop.set()
        return False

    monkeypatch.setattr(b.subprocess, "Popen", Child)
    monkeypatch.setattr(wake, "wait", wait)
    b._analysis_worker()
    assert launches == [True, True, True, False, True, False]
    assert waits == [(60, True), (60, False)]


def test_dashboard_job_profile_rejects_explorer_and_foreign_status(monkeypatch):
    monkeypatch.setenv("BRIDGE_PROFILE", "dashboard-green-days-readonly")
    b = _load()
    dashboard = {"command": "outcome-associations", "args": [
        "--outcome", "subjective.day_rating", "--mode", "green-vs-non-green",
        "--days", "365", "--min-n", "30", "--interactions", "none", "--top", "3",
    ]}
    assert b._analysis_request(json.dumps(dashboard)) is None
    foreign = {**dashboard, "args": [*dashboard["args"][:-1], "30"]}
    assert b._analysis_request(json.dumps(foreign)) is not None
    monkeypatch.setattr(b, "_audit", lambda _: None)
    from types import SimpleNamespace
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"request": foreign, "result": {"private": "hidden"}}), stderr=""))
    result = b.handle("analysis-job-status", ["a" * 32], 123)
    assert not result["ok"]
    assert result["stdout"] == ""
    assert "hidden" not in json.dumps(result)


def test_capture_commands_accept_only_the_new_source_flag_shape():
    b = _load()
    assert b._validate("day-rating", ["green", "--source", "panel-ui"]) is None
    assert b._validate("log-commitment", ["kept", "--source", "chat-panel"]) is None
    assert b._validate("checkin", ["mood", "4", "--source", "chat-telegram"]) is None
    assert b._validate("day-rating", ["green", "--source-id", "x"]) is not None

    assert b._validate("eat", ["sample-stew", "--time", "07:05", "--meal-type", "breakfast",
                               "--source", "panel-ui"]) is None
    assert b._validate("log-food", ["sample-stew", "--capture-id", "x"]) is not None


def test_broker_accepts_muscle_radar_argv():
    """Seam guard (§2a): the broker must accept the EXACT argv the panel's
    /api/training/muscle-radar route sends — panel tests stub bridge.run and
    toolkit tests call health.py directly, so only this pins the middle."""
    b = _load()
    assert b._validate("muscle-volume", ["--by", "group", "--days", "7"]) is None


def test_broker_accepts_strength_balance_lens_argv():
    """Seam guard (§3f Phase 2): the broker must accept the EXACT argv the
    panel's /api/training/muscle-map route sends for the strength-balance
    lens — panel tests stub bridge.run and toolkit tests call health.py
    directly, so only this pins the middle."""
    b = _load()
    assert b._validate("muscle-map",
                       ["--lens", "strength-balance", "--side-mode", "lr"]) is None
    # --side-mode is the ONLY new flag; anything else stays refused
    assert b._validate("muscle-map", ["--evil", "x"]) is not None


def test_store_true_flag_does_not_swallow_next_token():
    b = _load()
    # --all takes no value; a following bad flag must still be caught
    assert b._validate("commitment-list", ["--all", "--evil"]) is not None
    assert b._validate("feature-frame", ["--all", "--evil"]) is not None
    assert b._validate("feature-frame", ["--include-provenance", "--evil"]) is not None
    assert b._validate("feature-frame", ["--all=true"]) is not None
    assert b._validate("outcome-associations", ["--all", "--evil"]) is not None
    assert b._validate("finding-evidence", ["--all=true"]) is not None
    assert b._validate("hypothesis-promote", ["--all", "--evil"]) is not None


def test_readiness_accepts_only_zero_flags_or_one_bounded_range():
    b = _load()
    assert b._validate("readiness", []) is None
    assert b._validate(
        "readiness",
        ["--from", "2026-03-02", "--anchor", "2026-06-30"],
    ) is None
    assert b._validate("readiness", ["--anchor", "2026-06-30"]) is not None
    assert b._validate(
        "readiness",
        ["--from", "2026-07-01", "--anchor", "2026-06-30"],
    ) is not None


def test_governed_analysis_surface_rejects_unrestricted_writers_and_flags():
    b = _load()
    assert "correlate" not in b.ALLOWED
    assert "correlate" not in client_bridge.ALLOWED
    assert "day-signature" not in b.ALLOWED
    assert "day-signature" not in client_bridge.ALLOWED
    for writer in (
        "goal-set", "collector-run-record", "migrate", "event-log",
        "analysis-refresh", "finding-record", "hypothesis-refresh",
        "hypothesis-annotate", "synthesis-record", "hypothesis-evaluate",
        "synthesis-generate", "trigger-record",
        "insight-trigger-enqueue", "insight-trigger-claim",
        "insight-trigger-renew", "insight-trigger-complete",
        "insight-trigger-fail", "insight-notification-claim",
        "insight-notification-begin-dispatch", "insight-notification-ack",
        "insight-notification-fail", "insight-notification-resolve",
    ):
        assert writer not in b.ALLOWED
        assert writer not in client_bridge.ALLOWED
    assert b._validate(
        "outcome-associations",
        ["--outcome", "subjective.day_rating", "--mode", "all",
         "--from", "2026-01-01", "--to", "2026-06-30",
         "--min-n", "30", "--interactions", "pairwise", "--top", "10"],
    ) is None
    assert b._validate(
        "finding-evidence",
        ["--outcome", "subjective.day_rating", "--finding-id", "a" * 64,
         "--input-fingerprint", "b" * 64, "--all"],
    ) is None
    assert b._validate("finding-evidence", ["--row-id", "12"]) is not None
    assert b._validate(
        "hypothesis-promote",
        ["--outcome", "subjective.day_rating",
         "--finding-id", "sha256:" + "a" * 64,
         "--input-fingerprint", "sha256:" + "b" * 64,
         "--all"],
    ) is None
    assert b._validate("hypothesis-promote", ["--effect", "0.8"]) is not None
    assert b.TIMEOUTS["outcome-associations"] == 570
    assert b.TIMEOUTS["finding-evidence"] == 570
    assert b.TIMEOUTS["hypothesis-promote"] == 110


def test_audit_redacts_why_and_note_values():
    b = _load()
    red = b._audit_argv("log-commitment", ["kept", "--why", "very private reason"])
    assert "very private reason" not in red
    assert any(x.startswith("sha256:") for x in red)
    red2 = b._audit_argv("checkin", ["energy", "4", "--note", "secret"])
    assert "secret" not in red2


def test_phase5_bridge_validates_identifiers_ranges_cursors_and_positions():
    b = _load()
    finding = "sha256:" + "a" * 64
    fingerprint = "sha256:" + "b" * 64
    hypothesis = "sha256:" + "c" * 64
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
            "--from", "2026-01-01", "--to", "2026-06-30",
        ],
    ) is None
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
            "--from", "2026-06-30", "--to", "2026-01-01",
        ],
    ) is not None
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
            "--days", "36501",
        ],
    ) is not None
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
            "--all", "--days", "30",
        ],
    ) is not None
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
        ],
    ) is not None
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
            "--all",
        ],
    ) is not None
    # fork 2026-10-28: hypothesis-brief/hypotheses left the allowlist
    assert b._validate("hypothesis-brief", [hypothesis]) is not None
    assert b._validate("hypotheses", ["--status", "invented"]) is not None
    assert b._validate(
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding,
            "--input-fingerprint", fingerprint,
            "--days", "9" * 5000,
        ],
    ) is not None


def test_parse_allowed_uids_single_value_behaves_exactly_as_before(monkeypatch):
    """Single-value BRIDGE_ALLOWED_UID keeps the historical behavior: one
    trusted peer, byte-compatible with the old int(value) parsing."""
    from deploy.bridge_commands import parse_allowed_uids

    assert parse_allowed_uids(None) is None                       # unset -> default path
    assert parse_allowed_uids("1000") == frozenset({1000})        # exactly the old value
    assert parse_allowed_uids("0") == frozenset({0})


def test_parse_allowed_uids_accepts_a_set_and_refuses_everything_loose():
    from deploy.bridge_commands import parse_allowed_uids

    assert parse_allowed_uids("1000,10000") == frozenset({1000, 10000})

    for garbage in (
        "",            # empty listing
        "1000,",       # trailing comma
        ",1000",       # leading comma
        "1000, 10000", # space
        "1000,1000",   # duplicate
        "-1000",       # sign
        "1.5",         # float
        "0x10",        # hex
        "١٠٠٠",         # non-ASCII digits
        "panel",       # not an integer at all
    ):
        with pytest.raises(ValueError):
            parse_allowed_uids(garbage)


def test_broker_startup_fails_closed_on_unparseable_uid_list(monkeypatch):
    monkeypatch.setenv("BRIDGE_ALLOWED_UID", "1000, nope")
    with pytest.raises(ValueError):
        _load()._allowed_uids()   # the broker's Server() calls this before any socket opens


def test_broker_allowed_uids_fallback_and_set(monkeypatch):
    b = _load()
    monkeypatch.setenv("BRIDGE_ALLOWED_UID", "1000,10000")
    assert b._allowed_uids() == frozenset({1000, 10000})
    monkeypatch.setenv("BRIDGE_ALLOWED_UID", "10000")
    assert b._allowed_uids() == frozenset({10000})   # single value unchanged
    monkeypatch.delenv("BRIDGE_ALLOWED_UID", raising=False)
    with patch.object(b.pwd, "getpwnam", lambda name: type("R", (), {"pw_uid": 4242})()):
        assert b._allowed_uids() == frozenset({4242})   # unset -> default (panel uid)


def test_profile_and_targets_setters_pin_exact_flags():
    """Fork 2026-10-29 (docs/PRODUCT_AUTHORITY.md): profile-set/phase-set take
    no flags; nutrition-target-set takes only --target; planned-time-set
    takes only --tolerance. Positional values stay validated by health.py."""
    b = _load()
    assert b._validate("profile-set", ["sex", "male"]) is None
    assert b._validate("profile-set", ["sex", "male", "--evil", "x"]) is not None
    assert b._validate("phase-set", ["cut"]) is None
    assert b._validate("phase-set", ["cut", "--evil", "x"]) is not None
    assert b._validate("nutrition-target-set", ["kcal", "--target", "2200"]) is None
    assert b._validate("nutrition-target-set", ["kcal", "--evil", "2200"]) is not None
    assert b._validate("planned-time-set", ["dose", "08:00", "--tolerance", "30"]) is None
    assert b._validate("planned-time-set", ["dose", "08:00"]) is None  # --tolerance optional
    assert b._validate("planned-time-set", ["dose", "08:00", "--evil", "x"]) is not None


def test_daily_capture_setters_pin_exact_flags():
    """Fork 2026-10-29 (docs/PRODUCT_AUTHORITY.md "manual daily capture"):
    water-add/supplement-log/pain-log join the allowlist with exactly the
    flags health.py defines for them — no extra flag is accepted."""
    b = _load()
    assert b._validate("water-add", ["250"]) is None
    assert b._validate("water-add", ["250", "--date", "2026-10-29"]) is None
    assert b._validate("water-add", ["250", "--evil", "x"]) is not None
    assert b._validate("supplement-log", [
        "creatine", "--taken", "1", "--dose", "5", "--time", "08:00",
        "--date", "2026-10-29", "--note", "with breakfast", "--source", "manual",
    ]) is None
    assert b._validate("supplement-log", ["creatine", "--evil", "x"]) is not None
    assert b._validate("pain-log", [
        "low-back", "--intensity", "4", "--side", "left", "--quality", "ache",
        "--pattern", "morning", "--flags", "", "--note", "stiff on waking",
        "--date", "2026-10-29", "--source", "manual",
    ]) is None
    assert b._validate("pain-log", ["low-back", "--evil", "x"]) is not None


def test_audit_redacts_supplement_and_pain_notes():
    """The generic REDACT_FLAGS = {--why, --note} covers every command's
    --note, so supplement-log/pain-log need no command-specific redaction."""
    b = _load()
    red = b._audit_argv("supplement-log", ["creatine", "--note", "private note"])
    assert "private note" not in red
    red2 = b._audit_argv("pain-log", ["low-back", "--note", "private note"])
    assert "private note" not in red2


def test_self_service_profile_merge_refuses_owner_unknown_user_and_arguments():
    """user-profile-merge (SELF_SERVICE_COMMANDS) targets the CALLING `user`,
    never an argument — unlike ADMIN_COMMANDS. See _validate_self_service_command."""
    b = _load()
    assert "user-profile-merge" not in b.ADMIN_COMMANDS
    assert "user-profile-merge" in b.SELF_SERVICE_COMMANDS
    # No BRIDGE_USERS_ROOT configured in this process (single-user default).
    assert b.BRIDGE_USERS_ROOT in (None, "")
    assert b._validate("user-profile-merge", [], "alice") is not None
    assert "multi-user mode" in b._validate("user-profile-merge", [], "alice")
    assert b._validate("user-profile-merge", ["stray-arg"], "alice") is not None


def test_self_service_profile_merge_refuses_owner_and_unknown_user_with_users_root(
        tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_USERS_ROOT", str(tmp_path))
    (tmp_path / "alice").mkdir()
    b = _load()
    assert b._validate("user-profile-merge", [], None) is not None
    assert "bootstrap owner" in b._validate("user-profile-merge", [], "owner")
    assert "unknown or invalid user" in b._validate("user-profile-merge", [], "ghost")
    assert b._validate("user-profile-merge", [], "alice") is None


def test_meal_commands_take_stdin_or_one_id_and_no_flags():
    b = _load()
    # meal-save carries its JSON on stdin; meal-delete takes one exact recipe id
    assert b._validate("meal-save", []) is None
    assert b._validate("meal-save", ["--evil", "x"]) is not None
    assert b._validate("meal-delete", ["sample-stew"]) is None
    assert b._validate("meal-delete", ["sample-stew", "--force"]) is not None
    # the bulk recipe import is still not reachable
    assert b._validate("import-recipes", ["recipes.csv"]) is not None
