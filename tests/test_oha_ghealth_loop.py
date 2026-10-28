"""The `oha ghealth` container-mode scheduler loop and healthcheck helper
(deploy/docker/oha). Fork owner decision 2026-10-28 (docs/PRODUCT_AUTHORITY.md
"Fork deployment: scheduled Google Health collector container") authorizes
this loop. No network, no real subprocess beyond what each test fakes."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import signal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OHA = ROOT / "deploy/docker/oha"


def _load():
    loader = importlib.machinery.SourceFileLoader("test_oha_ghealth", str(OHA))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _FakeStopEvent:
    def __init__(self):
        self._flag = False

    def is_set(self):
        return self._flag

    def set(self):
        self._flag = True


# ── GHEALTH_INTERVAL_SECONDS parsing ────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, 21600),
    ("", 21600),
    ("3600", 3600),
    ("900", 900),
    ("1", 900),              # clamps to the 900s floor
    ("not-a-number", 21600),  # invalid falls back to the default
])
def test_interval_seconds_default_and_floor(raw, expected):
    mod = _load()
    assert mod._ghealth_interval_seconds(raw) == expected


# ── ghealth_loop: the extracted, fake-clock-testable loop body ─────────────

def test_stop_event_already_set_runs_nothing(tmp_path):
    mod = _load()
    stop_event = _FakeStopEvent()
    stop_event.set()
    calls = []
    mod.ghealth_loop(run_once=lambda: (calls.append(1), (0, 0.0))[1],
                     sleep=lambda *a: None, stop_event=stop_event,
                     interval_seconds=5, heartbeat_path=str(tmp_path / "hb"))
    assert calls == []


def test_failed_run_does_not_exit_loop_and_is_retried(tmp_path):
    mod = _load()
    heartbeat = tmp_path / "hb"
    results = iter([(1, 0.1), (1, 0.2), (0, 0.1)])
    calls, sleeps = [], []
    stop_event = _FakeStopEvent()

    def fake_run_once():
        rc, dur = next(results)
        calls.append(rc)
        return rc, dur

    def fake_sleep(seconds, ev):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            ev.set()

    mod.ghealth_loop(run_once=fake_run_once, sleep=fake_sleep, stop_event=stop_event,
                     interval_seconds=42, heartbeat_path=str(heartbeat))

    assert calls == [1, 1, 0]          # two failures, never exits, third run succeeds
    assert sleeps == [42, 42, 42]      # same interval every cycle, pass/fail alike
    assert heartbeat.exists()          # only the eventual success wrote it


def test_failed_run_never_writes_heartbeat(tmp_path):
    mod = _load()
    heartbeat = tmp_path / "hb"
    stop_event = _FakeStopEvent()

    def fake_sleep(seconds, ev):
        ev.set()

    mod.ghealth_loop(run_once=lambda: (1, 0.05), sleep=fake_sleep, stop_event=stop_event,
                     interval_seconds=10, heartbeat_path=str(heartbeat))
    assert not heartbeat.exists()


def test_signal_arriving_during_sleep_stops_loop_promptly(tmp_path):
    """Simulates SIGTERM firing while the loop is sleeping: the next sleep
    call sets stop_event, exactly like the real signal handler would."""
    mod = _load()
    calls = []
    stop_event = _FakeStopEvent()

    def fake_run_once():
        calls.append(1)
        return 0, 0.01

    def fake_sleep(seconds, ev):
        ev.set()  # a SIGTERM arrived mid-sleep

    mod.ghealth_loop(run_once=fake_run_once, sleep=fake_sleep, stop_event=stop_event,
                     interval_seconds=5, heartbeat_path=str(tmp_path / "hb"))
    assert calls == [1]  # exactly one run before the signal stopped it


# ── _ghealth_health_ok: pure healthcheck decision ───────────────────────────

def test_health_ok_within_startup_grace_even_without_heartbeat():
    mod = _load()
    now = 10_000
    assert mod._ghealth_health_ok(heartbeat_mtime=None, started_mtime=now - 60,
                                  interval_seconds=21600, now=now) is True


def test_health_ok_fresh_heartbeat_after_grace_window_elapsed():
    mod = _load()
    now = 1_000_000
    assert mod._ghealth_health_ok(heartbeat_mtime=now - 100, started_mtime=now - 100_000,
                                  interval_seconds=21600, now=now) is True


def test_health_not_ok_stale_heartbeat_and_old_start():
    mod = _load()
    now = 1_000_000
    threshold = 2 * 21600 + mod.GHEALTH_STARTUP_GRACE_SECONDS
    assert mod._ghealth_health_ok(heartbeat_mtime=now - threshold - 1,
                                  started_mtime=now - 100_000,
                                  interval_seconds=21600, now=now) is False


def test_health_not_ok_no_heartbeat_and_old_start():
    mod = _load()
    now = 1_000_000
    assert mod._ghealth_health_ok(heartbeat_mtime=None, started_mtime=now - 100_000,
                                  interval_seconds=21600, now=now) is False


def test_health_cli_reads_real_files(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "GHEALTH_HEARTBEAT_FILE", str(tmp_path / "hb"))
    monkeypatch.setattr(mod, "GHEALTH_STARTED_FILE", str(tmp_path / "started"))
    monkeypatch.delenv("GHEALTH_INTERVAL_SECONDS", raising=False)

    assert mod._ghealth_health_cli() == 1  # neither file exists yet

    mod._touch(str(tmp_path / "started"))
    assert mod._ghealth_health_cli() == 0  # inside the startup grace window

    mod._touch(str(tmp_path / "hb"))
    assert mod._ghealth_health_cli() == 0  # also healthy once a heartbeat lands


# ── SIGTERM wired through the real signal module via _ghealth_serve ────────

def test_ghealth_serve_stops_promptly_on_real_sigterm(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "GHEALTH_HEARTBEAT_FILE", str(tmp_path / "hb"))
    monkeypatch.setattr(mod, "GHEALTH_STARTED_FILE", str(tmp_path / "started"))
    monkeypatch.delenv("GHEALTH_INTERVAL_SECONDS", raising=False)

    calls = []

    def fake_cycle():
        calls.append(1)
        os.kill(os.getpid(), signal.SIGTERM)
        return 0, 0.01

    monkeypatch.setattr(mod, "_run_ghealth_cycle", fake_cycle)
    old_handler = signal.getsignal(signal.SIGTERM)
    try:
        rc = mod._ghealth_serve()
    finally:
        signal.signal(signal.SIGTERM, old_handler)

    assert rc == 0
    assert calls == [1]  # the SIGTERM during the first run stopped it before any sleep
    assert (tmp_path / "hb").exists()
    assert (tmp_path / "started").exists()


# ── _run_ghealth_cycle: container-mode env and dispatch wiring ─────────────

def test_run_ghealth_cycle_sets_container_mode_env(monkeypatch):
    mod = _load()
    captured = {}

    def fake_run(argv, env=None):
        captured["argv"] = argv
        captured["env"] = env
        import types
        return types.SimpleNamespace(returncode=3)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    returncode, duration = mod._run_ghealth_cycle()

    assert returncode == 3
    assert duration >= 0
    assert captured["env"]["HERMES_GHEALTH_MODE"] == "container"
    assert captured["argv"][-1] == mod.GHEALTH_SYNC_PATH


def test_main_dispatches_ghealth_once_serve_and_health(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.os, "chdir", lambda path: None)
    calls = []
    monkeypatch.setattr(mod, "_ghealth_once", lambda: calls.append("once") or 0)
    monkeypatch.setattr(mod, "_ghealth_serve", lambda: calls.append("serve") or 0)
    monkeypatch.setattr(mod, "_ghealth_health_cli", lambda: calls.append("health") or 0)

    assert mod.main(["ghealth", "--once"]) == 0
    assert mod.main(["ghealth"]) == 0
    assert mod.main(["ghealth-health"]) == 0
    assert calls == ["once", "serve", "health"]
