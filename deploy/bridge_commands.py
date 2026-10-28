"""Code-owned command and flag declarations for both bridge processes.

The source-tree panel imports this module as deploy.bridge_commands. The broker
imports the adjacent installed copy, which must stay root-owned and read-only
alongside its executable. This module contains declarations only: the broker
owns privileged validation, profiles, execution limits and audit handling.

It also owns the per-user profile.json whitelist and its validation (see
docs/MULTI_USER.md "Per-user profile settings") — the ONE place both the
broker's child-env overlay and the panel's read-only display helper
(app.users.current_profile) resolve the same keys the same way.
"""
import errno
import json
import os
import stat

# Positional values and command semantics remain validated by health.py or
# hermesctl. Commands intentionally absent here remain unavailable to the panel.
COMMAND_FLAGS = {
    "today":         {"--date"},
    "summary":       {"--days"},
    "schema":        set(),
    "query":         set(),
    "menu":          {"--date"},
    "last-session":  set(),
    "muscle-volume": {"--source", "--days", "--by"},
    "bp-brief":      {"--days", "--drug"},
    "day-rating":    {"--date", "--source"},
    "log-set":       {"--weight", "--reps", "--rpe", "--set-index", "--date",
                      "--workout-title", "--set-type"},
    "routine-set":   {"--sets", "--reps", "--weight", "--order"},
    "routine-remove": set(),
    "routine-undo":  set(),
    "schedule-set":  set(),
    "write-note":    set(),   # content arrives on stdin, not as flags
    "log":           set(),
    # insight engine: read-only statistics computed in health.py (no writes)
    "build-daily-frame": {"--days"},
    "features":          {"--days"},
    "adherence":         {"--days"},
    "data-coverage":     {"--days"},
    "scores":            {"--days"},
    # follow-through capture (validated writes, upsert — nothing deletes)
    "commitment-set":  {"--identity", "--trigger", "--floor", "--reward", "--active"},
    "commitment-list": {"--all"},
    "log-commitment":  {"--id", "--why", "--date", "--source"},
    "checkin":         {"--time", "--note", "--date", "--source"},
    "eat":           {"--portions", "--date", "--time", "--meal-type", "--source"},
    "log-food":      {"--grams", "--date", "--time", "--meal-type", "--source"},
    "set-batch":     {"--grams"},
    "prep":          {"--portions", "--batch-grams"},
    # §3e/§3c/§3d — READS ONLY. Capture is Telegram-only and the panel is
    # analysis-only; the writes (fitness-test-log, athletic-target-set,
    # fitness-test-void) go through the agent's own health.py path / SSH, never
    # the sandboxed panel — minimal attack surface.
    "fitness-tests":       {"--days"},
    "athletic-radar":      set(),
    "strength-ratios":     {"--view"},
    # §3f V-taper — read-only (import-hevy-body is collector-only, NOT here)
    "vtaper":              {"--days"},
    # §3b drill-down — read-only (map seeding is SSH/agent-path, NOT here)
    "muscle-detail":       {"--days"},
    # §3f muscle figure — read-only display payload (map authored in health.py;
    # --side-mode is the Phase-2 strength-balance Combined|L/R sub-view)
    "muscle-map":          {"--lens", "--days", "--side-mode"},
    # §4b timing adherence — read-only (planned-time-set is NOT here)
    "timing-adherence":    {"--days"},
    # §labs bloodwork — read-only view feed (capture, OCR ingestion and the
    # cited catalog seed are collector-only, NOT here)
    "labs":                {"--days", "--test", "--panel"},
    # §body-composition multi-frequency BIA report (fork owner decision
    # 2026-10-27, docs/PRODUCT_AUTHORITY.md): validated write (JSON on
    # stdin, no flags) + a bounded read.
    "log-body-composition": set(),
    "body-composition":     {"--days", "--latest"},
    # T44 targets engine — read-only compute, zero flags (profile-set /
    # phase-set / nutrition-target-set are owner config, NOT here)
    "nutrition-targets":   set(),
    # T46 target-coverage score — read-only, shares the T44 targets engine;
    # value whitelist (7/30/90/365) is enforced by health.py's own argparse
    # choices, this flag whitelist is defense in depth
    "nutrition-coverage":  {"--days"},
    # T48 readiness engine — read-only. The fixed range is server-selected for
    # fictional dashboard parity; browser requests cannot supply these flags.
    "readiness":           {"--from", "--anchor"},
    # Phase 3 unified integration — deterministic reads only. The goal,
    # collector-run and migration writers remain deliberately absent.
    "feature-registry": {"--family"},
    "feature-frame": {"--from", "--to", "--days", "--all", "--family",
                      "--include-provenance"},
    "data-readiness": {"--from", "--to", "--days", "--all", "--goal", "--outcome"},
    "goal-list": {"--all"},
    # Phase 4 deterministic association engine — pure reads only.
    "outcome-associations": {
        "--outcome", "--from", "--to", "--days", "--all", "--mode",
        "--min-n", "--interactions", "--top",
    },
    "finding-evidence": {
        "--outcome", "--finding-id", "--input-fingerprint",
        "--from", "--to", "--days", "--all",
    },
    # Private derived-analysis jobs: fixed closed request JSON on stdin;
    # status accepts only an opaque ID. Internal worker commands stay absent.
    "analysis-job-start": set(),
    "analysis-job-status": set(),
    # Phase 5 ledger/synthesis. Identifier-only promotion recomputes the
    # finding in health.py. Every unrestricted writer remains absent.
    "hypothesis-promote": {
        "--outcome", "--finding-id", "--input-fingerprint",
        "--from", "--to", "--days", "--all",
    },
    "hypotheses": {"--status", "--outcome", "--limit", "--before"},
    "hypothesis-brief": set(),
    "synthesis-history": {"--limit", "--before"},
    # Phase 6 read-only redacted orchestration audit. Queue/outbox writers and
    # every token-bearing command remain absent.
    "insight-run-status": {"--limit"},
    # agent/model operations -> hermesctl (runs as hermes), not health.py
    "hermes-status":    set(),
    "hermes-spend":     set(),
    "hermes-model-set": set(),
    "fitbit-status":    set(),
    "hermes-chat":      {"--session"},   # message on stdin; fixed session validated by hermesctl
    # Admin panel user lifecycle (docs/MULTI_USER.md "Admin UI"): one
    # positional target user id, zero flags. These never exec health.py or
    # hermesctl — the broker handles them in-process (see ADMIN_COMMANDS and
    # deploy/hermes-bridge's _run_admin_command) because they create/manage a
    # user's OWN directory tree, which the HEALTH_DB/HEALTH_VAULT env-var
    # model has no way to express before that user exists.
    "user-provision":      set(),
    "user-profile-write":  set(),   # profile JSON arrives on stdin
    "user-profile-clear":  set(),
}

# Admin-only commands executed directly by the broker process (no health.py /
# hermesctl subprocess). Every one operates on a target user id — never the
# caller's own — validated against USER_ID_RE and refused for the bootstrap
# owner. See deploy/hermes-bridge's _validate_admin_command/_run_admin_command.
ADMIN_COMMANDS = frozenset({"user-provision", "user-profile-write", "user-profile-clear"})

# Preserve the existing client-side flag-check subset. The broker checks flags
# for every command; extending client checks is a separate behavior change.
CLIENT_FLAG_CHECKS = {
    "day-rating", "log-commitment", "checkin", "eat", "log-food", "readiness",
    "feature-registry", "feature-frame", "data-readiness", "goal-list",
    "outcome-associations", "finding-evidence", "hypothesis-promote",
    "analysis-job-start", "analysis-job-status",
    "hypotheses", "hypothesis-brief", "synthesis-history", "insight-run-status",
}

# store_true flags consume no following value; other allowed flags consume one.
NO_VALUE_FLAGS = {"--all", "--include-provenance", "--latest"}

# Comprehensive fictional reads on the capped one-CPU host took 449s for
# all-mode Explorer and 443s for full pairwise evidence replay. The finite 570s
# budget includes 25% headroom, rounded up to 30s. Write and external-Hermes
# limits stay independent; the panel waits ten seconds longer for a response.
OUTCOME_ANALYSIS_TIMEOUT = 570


def parse_allowed_uids(value):
    """`BRIDGE_ALLOWED_UID` as a SET of peer uids, or None when unset.

    The default (unset) keeps the historical behavior everywhere: the
    caller falls back to the single trusted service account (the panel).
    One bare int is exactly the old single-value behavior; a comma-separated
    listing authorizes several trusted peers (panel + additional clients).
    Strict: every entry is an unsigned ASCII integer, no spaces, no signs,
    no duplicates, never an empty listing — anything else is a configuration
    error the broker must fail closed on (ValueError at startup). Both the
    broker (peer check) and any client that must mirror the same trust shape
    use this one parser so they cannot drift apart."""

    if value is None:
        return None
    if value == "":
        raise ValueError("BRIDGE_ALLOWED_UID is set but empty")
    uids = []
    for part in value.split(","):
        if not part.isascii() or not part.isdigit() or not str(int(part)) == part:
            raise ValueError(
                f"BRIDGE_ALLOWED_UID entries must be bare integers, got {part!r}"
            )
        uid = int(part)
        if uid in uids:
            raise ValueError(f"BRIDGE_ALLOWED_UID lists uid {uid} more than once")
        uids.append(uid)
    return frozenset(uids)


# ── per-user profile.json (docs/MULTI_USER.md "Per-user profile settings") ──
# The bootstrap owner keeps reading these straight off the installation
# process environment, exactly as a single-user install always has.
# HERMES_TIMEZONE is deliberately absent: it is bound at panel import time,
# so a per-user value would mix civil dates within one process and corrupt
# day-boundary writes — it stays installation-wide.
PROFILE_KEYS = frozenset({
    "HERMES_NUTRIENT_TARGETS_JSON",
    "HERMES_PHASE_OFFSETS_JSON",
    "HERMES_PROTEIN_PER_KG_JSON",
    "HERMES_WATER_FALLBACK_ML",
    "HERMES_WATER_ML_PER_KG",
    "HERMES_WATER_ML_PER_EXERCISE_HOUR",
    "HERMES_WATER_HOT_DAY_BONUS_ML",
    "HERMES_WATER_HOT_DAY_TEMP_C",
    "HERMES_SIT_REACH_NORMAL_CM",
    "HERMES_PRIMARY_MEDICATION",
    "HERMES_MEDICATION_ALIASES",
})

# Keys whose profile.json value is a JSON object/array (serialized with
# json.dumps into the child's env string); every other whitelisted key is a
# plain string/number, stringified as-is.
PROFILE_JSON_KEYS = frozenset({
    "HERMES_NUTRIENT_TARGETS_JSON",
    "HERMES_PHASE_OFFSETS_JSON",
    "HERMES_PROTEIN_PER_KG_JSON",
})

PROFILE_MAX_BYTES = 16 * 1024


class ProfileError(ValueError):
    """A profile.json object fails the whitelist — message is safe to audit."""


def validate_profile(data) -> None:
    """Raise ProfileError on the first violation; otherwise do nothing.
    `data` is an already-parsed JSON value (not yet known to be an object)."""
    if not isinstance(data, dict):
        raise ProfileError("profile.json must be a JSON object")
    for key, value in data.items():
        if key not in PROFILE_KEYS:
            raise ProfileError(f"profile key not allowed: {key}")
        if key in PROFILE_JSON_KEYS:
            if not isinstance(value, (dict, list)):
                raise ProfileError(f"profile key {key} must be a JSON object or array")
        elif isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProfileError(f"profile key {key} must be a string or number")


def profile_env_overlay(data) -> dict:
    """A validated profile.json object -> the child-process env overlay
    (every value a str; *_JSON keys re-serialized with json.dumps). Raises
    ProfileError if `data` fails validate_profile."""
    validate_profile(data)
    return {
        key: (json.dumps(value) if key in PROFILE_JSON_KEYS else str(value))
        for key, value in data.items()
    }


def read_profile_file(path):
    """Read and JSON-parse `path`. Returns ({}, None) when the file is
    simply absent (no per-user overrides — not a refusal). Returns
    (data, None) with the parsed value otherwise (not yet whitelist-checked).
    Returns (None, error) for anything that must fail closed: a symlink
    (checked via O_NOFOLLOW, race-free), a non-regular file, a file over
    PROFILE_MAX_BYTES, or content that isn't valid UTF-8 JSON."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None, "profile.json must not be a symlink"
        return None, f"profile.json unreadable: {exc.strerror or exc}"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "profile.json must be a regular file"
        if st.st_size > PROFILE_MAX_BYTES:
            return None, "profile.json exceeds the 16 KiB limit"
        raw = os.read(fd, PROFILE_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > PROFILE_MAX_BYTES:
        return None, "profile.json exceeds the 16 KiB limit"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "profile.json is not valid JSON"
    return data, None


def load_profile(path):
    """read_profile_file + whitelist validation in one call. Returns
    (overlay, None) — overlay is {} for an absent file (installation
    defaults) — or (None, error) for a present-but-invalid file. Never a
    silent partial overlay: either every key in `data` is valid, or none of
    it is used."""
    data, err = read_profile_file(path)
    if err is not None:
        return None, err
    try:
        return profile_env_overlay(data), None
    except ProfileError as exc:
        return None, str(exc)
