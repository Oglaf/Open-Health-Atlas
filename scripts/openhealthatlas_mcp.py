#!/usr/bin/env python3
"""Local stdio MCP access to bounded OpenHealthAtlas evidence.

The caller selects an existing local database at startup. No model, network
listener, provider credentials, database initialization or migration runs here.
Install requirements-mcp.txt separately from the ordinary product dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone as utc_timezone
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import time
from pathlib import Path
import sys
from typing import Any


MAX_REQUEST_BYTES = 131_072
MAX_RESULT_BYTES = 1_048_576
MAX_TOOL_BYTES = MAX_RESULT_BYTES + 4096
WORKER_TIMEOUT_SECONDS = 570
MAX_CACHED_SURFACES = 32

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "toolkit"))

# Multi-user mode (see docs/MULTI_USER.md): same id shape the broker accepts
# in deploy/hermes-bridge's USER_ID_RE. The client never supplies a path; this
# is only a server-side lookup key into --users-root.
USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\Z")
OWNER_USER_ID = "owner"
USER_FIELD = {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,31}$", "maxLength": 32}

# Broker-mediated ingest (health_write) — the ONLY write surface this stdio
# server can ever expose, and only when the operator configured a private
# Unix-socket broker (`--bridge-socket`, which requires `--users-root`). The
# tool is a thin structured-JSON client for the broker the panel already uses:
# it never opens SQLite for a write and never writes a file itself. Every call
# — allowed or refused — is audited by the broker's own handle() audit trail.
# docs/PRODUCT_AUTHORITY.md records the owner decision for this fork (2026-10-27).
HEALTH_WRITE_COMMANDS = (
    "eat", "log-food", "log-set", "routine-set", "routine-remove",
    "routine-undo", "checkin", "day-rating", "write-note", "commitment-set",
    "commitment-list", "log-commitment", "schedule-set", "set-batch", "prep",
)
NO_BROKER_REFUSAL = (
    "no broker configured; ingest is unavailable in this deployment"
)
HEALTH_WRITE_DESCRIPTION = (
    "Log a validated health record (food, training set, check-in, day rating, "
    "note, commitment, routine or schedule) for one named user through the "
    "deployment's audited write broker. Only the broker's whitelisted write "
    "commands are accepted; refusals are returned unchanged."
)
MAX_WRITE_STDIN_BYTES = 4096      # free text (write-note) stays small
MAX_WRITE_POSITIONALS = 8
MAX_WRITE_FLAG_VALUES = 20
MAX_WRITE_ARGV = 40               # == deploy/hermes-bridge's MAX_ARGS


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


def _strings(maximum: int, minimum: int = 0) -> dict:
    return {"type": "array", "items": {"type": "string"},
            "minItems": minimum, "maxItems": maximum, "uniqueItems": True}


DATE_RANGE = _object({
    "from": {"type": "string", "format": "date",
             "description": "Inclusive start, YYYY-MM-DD."},
    "to": {"type": "string", "format": "date",
           "description": "Inclusive end, YYYY-MM-DD; at most 366 days."},
}, ("from", "to"))
EVIDENCE_REFERENCE = _object({
    "contract": {"type": "string", "const": "openhealthatlas-evidence-ref-v1"},
    "operation": {"type": "string", "enum": [
        "health_catalog", "health_query", "health_analyze",
    ]},
    "arguments": {"type": "object",
                  "description": "Copy the complete returned arguments unchanged."},
    "result_id": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    "finding_id": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$",
                   "description": "Copy only when present in the reference."},
}, ("contract", "operation", "arguments", "result_id"))

TOOL_SCHEMAS = {
    "health_catalog": _object({
        "search": {"type": "string", "maxLength": 80},
        "domains": _strings(12), "roles": _strings(8),
        "availability": {**_strings(4), "items": {"type": "string", "enum": [
            "implemented", "not_implemented", "connected", "not_connected",
        ]}},
        "cursor": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
    }),
    "health_query": _object({
        "feature_keys": _strings(32, 1), "range": DATE_RANGE,
        "view": {"type": "string", "enum": [
            "latest", "series", "summary", "period_compare",
        ], "default": "latest"},
        "grain": {"type": "string", "enum": ["day"]},
        "aggregates": {**_strings(7), "items": {"type": "string", "enum": [
            "count", "min", "max", "mean", "median", "sum", "stddev",
        ]}},
    }, ("feature_keys", "range")),
    "health_analyze": _object({
        "outcome_key": {"type": "string"}, "exposure_keys": _strings(32, 1),
        "range": DATE_RANGE,
        "mode": {"type": "string", "enum": [
            "all", "ordinal", "green-vs-non-green", "red-vs-non-red",
        ], "default": "all"},
        "interactions": {"type": "boolean", "default": False},
        "min_n": {"type": "integer", "minimum": 30, "maximum": 500, "default": 30},
        "top": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
    }, ("outcome_key", "exposure_keys", "range")),
    "health_evidence": _object({
        "evidence_refs": {"type": "array", "minItems": 1, "maxItems": 20,
                          "items": EVIDENCE_REFERENCE},
        "detail": {"type": "string", "enum": ["summary", "lineage"],
                   "default": "summary"},
    }, ("evidence_refs",)),
}
TOOL_DESCRIPTIONS = {
    "health_catalog": "Discover registered health features and availability. No SQL or paths.",
    "health_query": "Read registered features as latest, daily series, summary or period comparison. Preserves source, time, units and missingness.",
    "health_analyze": "Calculate bounded deterministic associations for an outcome and explicit exposures. Associations do not establish causation.",
    "health_evidence": "Replay one to 20 complete evidence_refs objects from earlier results before explaining them. Copy contract, operation, arguments, result_id and optional finding_id unchanged. At most one analysis reference. Hash strings alone are invalid.",
}
DEFAULTS = {
    "health_catalog": {"limit": 20},
    "health_query": {"view": "latest"},
    "health_analyze": {"mode": "all", "interactions": False, "min_n": 30, "top": 10},
    "health_evidence": {"detail": "summary"},
}


def _with_user(schema: dict) -> dict:
    """The multi-user variant of one tool's schema: adds a required `user`
    lookup key and nothing else. additionalProperties stays False."""
    return {
        **schema,
        "properties": {**schema["properties"], "user": USER_FIELD},
        "required": [*schema["required"], "user"],
    }


MULTI_USER_TOOL_SCHEMAS = {name: _with_user(schema) for name, schema in TOOL_SCHEMAS.items()}

HEALTH_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "user": USER_FIELD,
        "command": {
            "type": "string", "maxLength": 64,
            "description": "One whitelisted validated broker writer (eat, log-food, "
                           "log-set, routine-set, routine-remove, routine-undo, "
                           "checkin, day-rating, write-note, commitment-set, "
                           "commitment-list, log-commitment, schedule-set, "
                           "set-batch, prep).",
        },
        "positional": {
            "type": "array", "items": {"type": "string"}, "maxItems": MAX_WRITE_POSITIONALS,
            "description": "The command's positional values, in order (e.g. the recipe "
                           "name for eat/log-food, the exercise for log-set, the note "
                           "path for write-note).",
        },
        "args": {
            "type": "object", "maxProperties": MAX_WRITE_FLAG_VALUES,
            "additionalProperties": {"type": ["string", "number", "boolean"]},
            "description": "Flag -> value, keyed by the broker's exact flag names "
                           "(e.g. {\"--portions\": 1}); store-true flags take JSON true.",
        },
        "stdin": {
            "type": "string", "maxLength": MAX_WRITE_STDIN_BYTES,
            "description": "Free text, accepted only for write-note (up to 4 KiB).",
        },
    },
    "required": ["user", "command"],
    "additionalProperties": False,
}

_BRIDGE_COMMANDS: Any = None


def bridge_commands_module():
    """Load deploy/bridge_commands.py by path (declarations only; it imports
    nothing from the panel application, so Flask never enters this process).
    The health_write flag allowlist is derived from the shared COMMAND_FLAGS
    declaration so the MCP and the broker cannot drift apart."""
    global _BRIDGE_COMMANDS
    if _BRIDGE_COMMANDS is None:
        path = ROOT / "deploy" / "bridge_commands.py"
        loader = importlib.machinery.SourceFileLoader(
            "openhealthatlas_bridge_commands", str(path),
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
        _BRIDGE_COMMANDS = module
    return _BRIDGE_COMMANDS


def load_write_commands() -> dict[str, frozenset]:
    """command -> its exact broker flag set, for the restricted health_write
    exposure subset only."""
    module = bridge_commands_module()
    return {
        command: frozenset(module.COMMAND_FLAGS[command])
        for command in HEALTH_WRITE_COMMANDS
        if command in module.COMMAND_FLAGS
    }


def _write_symbol_flag(flag: str, value, allowed, argv: list[str]) -> str | None:
    """Translate one args entry to argv tokens; deterministic structured JSON
    in, the same argv shape the panel client builds. None on any violation."""
    if flag not in allowed:
        return f"flag not allowed: {flag}"
    if flag in bridge_commands_module().NO_VALUE_FLAGS:
        if value is not True:
            return f"flag takes no value: {flag}"
        argv.append(flag)
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return f"flag value must be a string, number, or boolean-true: {flag}"
    argv.extend([flag, str(value)])
    return None


def build_health_write_request(arguments, write_commands, user: object):
    """Validate a health_write call strictly and build the broker payload.
    Returns (payload, None) or (None, error). `user` is resolved by the caller;
    here it is only carried onto the wire like the panel does."""
    if not isinstance(arguments, dict):
        return None, "invalid arguments"
    command = arguments.get("command")
    if (isinstance(command, bool) or not isinstance(command, str)
            or command not in write_commands):
        return None, "command not allowed for health_write"
    flags_spec = write_commands[command]
    positional = arguments.get("positional")
    argv: list[str] = []
    if positional is not None:
        if (not isinstance(positional, list)
                or len(positional) > MAX_WRITE_POSITIONALS
                or any(isinstance(item, bool) or not isinstance(item, str)
                       or not item for item in positional)):
            return None, "invalid positional argument"
        argv.extend(positional)
    values = arguments.get("args")
    if values is not None:
        if not isinstance(values, dict) or len(values) > MAX_WRITE_FLAG_VALUES:
            return None, "invalid args object"
        for flag, value in values.items():
            if not isinstance(flag, str) or len(flag) > 64:
                return None, "invalid args object"
            error = _write_symbol_flag(flag, value, flags_spec, argv)
            if error is not None:
                return None, error
    if len(argv) > MAX_WRITE_ARGV:
        return None, "too many arguments"
    stdin = arguments.get("stdin")
    payload = {"subcmd": command, "args": argv, "user": user}
    if stdin is not None:
        if command != "write-note":
            return None, "stdin is only accepted for write-note"
        if not isinstance(stdin, str) or len(stdin.encode("utf-8")) > MAX_WRITE_STDIN_BYTES:
            return None, "stdin exceeds the 4 KiB write limit"
        payload["stdin"] = stdin
    return payload, None


def broker_exchange(socket_path: str, payload: dict, timeout: float = 65.0) -> dict:
    """One newline-terminated JSON request to the broker; one JSON line back.
    Pure stdio/stdlib: the exact wire protocol app/bridge.py speaks, without
    importing the panel application (never Flask). The broker's outcome JSON
    — including refusals — is returned verbatim."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall((json.dumps(payload, allow_nan=False) + "\n").encode("utf-8"))
        buf = bytearray()
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > MAX_RESULT_BYTES:
                return {"ok": False, "code": 1, "stdout": "", "stderr": "response too large"}
    if not buf:
        return {"ok": False, "code": 1, "stdout": "", "stderr": "bridge returned a malformed response"}
    try:
        response = json.loads(bytes(buf).decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "code": 1, "stdout": "", "stderr": "bridge returned a malformed response"}
    if not isinstance(response, dict) or not isinstance(response.get("code"), int):
        return {"ok": False, "code": 1, "stdout": "", "stderr": "bridge returned a malformed response"}
    return response


def _task_status_schema(multi_user: bool) -> dict:
    properties = {
        "task_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 20, "default": 5},
    }
    required: tuple[str, ...] = ("task_id",)
    if multi_user:
        properties["user"] = USER_FIELD
        required = (*required, "user")
    return _object(properties, required)


TASK_STATUS_DESCRIPTION = (
    "Wait up to wait_seconds for an analysis/evidence task. Completed result "
    "is a snapshot in result; a new invocation checks current data. No health "
    "records change."
)


def encode_result(value):
    from hermes_insights.contracts import canonical_json
    return canonical_json(value)


def execute_request(surface, request):
    from hermes_insights.hermes_surface import SurfaceError

    try:
        result = surface.execute(request)
        encoded = encode_result(result)
        if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("response too large")
        return result
    except SurfaceError as exc:
        return {"status": "refused", "error": {"code": exc.code, "message": str(exc)}}
    except Exception:
        return {"status": "refused", "error": "local evidence request failed closed"}


class AnalysisTasks:
    """One owned process, bounded in-memory results, and no persisted job state."""

    def __init__(self, database: Path, timezone: str):
        self.database, self.timezone = database, timezone
        self.tasks: dict[str, dict] = {}

    def _discard(self, task_id: str):
        task = self.tasks.pop(task_id, None)
        if task and "expiry" in task:
            task["expiry"].cancel()

    def _prune(self):
        completed = [key for key, task in self.tasks.items()
                     if task["status"] not in {"queued", "running"}]
        for key in completed:
            if time.monotonic() - self.tasks[key]["finished"] > 600:
                self._discard(key)
        completed = [key for key in completed if key in self.tasks]
        for key in completed[:-8]:
            self._discard(key)

    def submit(self, request: dict) -> dict:
        self._prune()
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            return {"status": "refused", "error": "request too large"}
        identity = hashlib.sha256(encoded.encode()).hexdigest()
        for task_id, task in self.tasks.items():
            if task["status"] in {"queued", "running"}:
                if task["identity"] == identity:
                    return self.status(task_id)
                return {"status": "refused", "error": "another analysis is running; check its task first"}
        task_id = secrets.token_hex(16)
        task = {"status": "queued", "identity": identity, "operation": request["operation"],
                "started_at": datetime.now(utc_timezone.utc).isoformat()}
        self.tasks[task_id] = task
        task["runner"] = asyncio.create_task(self._run(task_id, task, encoded))
        return self.status(task_id)

    async def _run(self, task_id: str, task: dict, encoded: str):
        process = None
        terminal_status = "failed"
        try:
            # Bundled launchers use isolated Python. Retain that boundary in
            # workers, and pass -B explicitly because -I ignores Python env.
            python_flags = (["-I"] if sys.flags.isolated else []) + ["-B"]
            process = await asyncio.create_subprocess_exec(
                sys.executable, *python_flags, str(Path(__file__).resolve()), "--database", str(self.database),
                "--timezone", self.timezone, "--worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            task["status"] = "running"
            process.stdin.write(encoded.encode())
            await process.stdin.drain()
            process.stdin.close()

            async def collect():
                stdout = await process.stdout.read(MAX_RESULT_BYTES + 1)
                if len(stdout) > MAX_RESULT_BYTES:
                    raise ValueError("worker response too large")
                # read(n) may return a short pipe chunk before EOF.
                while chunk := await process.stdout.read(MAX_RESULT_BYTES + 1 - len(stdout)):
                    stdout += chunk
                    if len(stdout) > MAX_RESULT_BYTES:
                        raise ValueError("worker response too large")
                await process.wait()
                return stdout

            stdout = await asyncio.wait_for(collect(), timeout=WORKER_TIMEOUT_SECONDS)
            if process.returncode != 0 or len(stdout) > MAX_RESULT_BYTES:
                raise ValueError("worker failed")
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError("invalid worker result")
            terminal_status = "completed"
            task["result"] = result
        except asyncio.CancelledError:
            terminal_status = "cancelled"
        except asyncio.TimeoutError:
            terminal_status = "failed"
            task["error"] = "analysis exceeded its execution limit"
        except Exception:
            terminal_status = "failed"
            task["error"] = "local analysis failed closed"
        finally:
            if process is not None:
                if process.returncode is None:
                    process.kill()
                # Drain after killing too: a paused full pipe can otherwise keep
                # asyncio Process.wait pending even after the child has exited.
                while await process.stdout.read(65_536):
                    pass
                await process.wait()
            task["finished"] = time.monotonic()
            task["completed_at"] = datetime.now(utc_timezone.utc).isoformat()
            task["status"] = terminal_status
            task["expiry"] = asyncio.get_running_loop().call_later(600, self._discard, task_id)
            self._prune()

    def status(self, task_id: str) -> dict:
        self._prune()
        task = self.tasks.get(task_id)
        if task is None:
            return {"status": "refused", "error": "task unavailable or expired"}
        response = {"task_id": task_id, "status": task["status"], "operation": task["operation"],
                    "started_at": task["started_at"]}
        if "completed_at" in task:
            response["completed_at"] = task["completed_at"]
        if "result" in task:
            response["result"] = task["result"]
        if "error" in task:
            response["error"] = task["error"]
        if task["status"] in {"queued", "running"}:
            response["poll_after_seconds"] = 5
        return response

    async def wait(self, task_id: str, seconds: int) -> dict:
        task = self.tasks.get(task_id)
        if task and task["status"] in {"queued", "running"}:
            try:
                await asyncio.wait_for(asyncio.shield(task["runner"]), timeout=seconds)
            except asyncio.TimeoutError:
                pass
        return self.status(task_id)

    async def close(self):
        runners = [task["runner"] for task in self.tasks.values()]
        for runner in runners:
            if not runner.done():
                runner.cancel()
        await asyncio.gather(*runners, return_exceptions=True)
        for task_id in list(self.tasks):
            self._discard(task_id)


_PANEL_READ_ACTIONS = {
    getattr(sqlite3, "SQLITE_READ", 20),
    getattr(sqlite3, "SQLITE_SELECT", 21),
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _panel_authorizer(action, _arg1, _arg2, _db_name, _trigger):
    return sqlite3.SQLITE_OK if action in _PANEL_READ_ACTIONS else sqlite3.SQLITE_DENY


class UserResolver:
    """Maps a client-supplied `user` id to that user's health.db, or refuses.

    `user` is a lookup key only; the client can never supply a path. Mirrors
    deploy/hermes-bridge's `_resolve_user_env`: same id pattern, realpath +
    commonpath containment against --users-root (no traversal, no symlink
    escape), and the directory must already exist with a health.db in it.
    `owner` is special: it maps to --owner-database (outside users-root,
    the legacy single-user database) and is otherwise unknown. When
    --panel-db is configured, every id (owner included) must also have an
    'active' row in its `users` table; an unreadable panel.db fails closed
    for every id, never silently falling back to "allow".
    """

    def __init__(self, users_root: Path, owner_database: Path | None, panel_db: Path | None):
        self.users_root = os.path.realpath(users_root)
        self.owner_database = owner_database
        self.panel_db = panel_db

    def _panel_allows(self, user: str) -> bool:
        if self.panel_db is None:
            return True
        try:
            con = sqlite3.connect(f"file:{self.panel_db}?mode=ro", uri=True)
            try:
                con.execute("PRAGMA query_only=ON")
                con.set_authorizer(_panel_authorizer)
                row = con.execute(
                    "SELECT status FROM users WHERE id=?", (user,),
                ).fetchone()
            finally:
                con.close()
        except Exception:
            return False
        return row is not None and row[0] == "active"

    def resolve(self, user: object) -> Path | None:
        if not isinstance(user, str) or USER_ID_RE.fullmatch(user) is None:
            return None
        if not self._panel_allows(user):
            return None
        if user == OWNER_USER_ID:
            if self.owner_database is None:
                return None
            database = Path(self.owner_database)
            return database if database.is_file() else None
        user_dir = os.path.realpath(os.path.join(self.users_root, user))
        if (
            user_dir == self.users_root
            or os.path.commonpath([self.users_root, user_dir]) != self.users_root
            or not os.path.isdir(user_dir)
        ):
            return None
        database = Path(user_dir) / "health.db"
        return database if database.is_file() else None


class SingleUserTarget:
    """One owned database for the life of the process — today's behavior,
    unchanged tool schemas, selected by --database."""

    multi_user = False

    def __init__(self, surface, tasks: AnalysisTasks):
        self.surface = surface
        self.tasks = tasks
        self.schemas = TOOL_SCHEMAS
        self.task_status_schema = _task_status_schema(False)

    def resolve(self, arguments: dict):
        return self.surface, self.tasks, arguments

    async def close(self):
        await self.tasks.close()

    def health_write_request(self, arguments):
        raise ValueError("unavailable operation")


class MultiUserTarget:
    """One process serves any user named per call, selected by --users-root.

    Per-user state is isolated: a LocalSurface (bounded, lazily created) per
    resolved database, and a separate AnalysisTasks registry per user id, so
    a task id minted for one user is never visible to another (it simply
    is not in that user's own registry) and one user's heavy analysis never
    blocks another's.
    """

    multi_user = True

    def __init__(self, resolver: UserResolver, timezone: str,
                 bridge_socket: str | None = None,
                 write_commands: dict[str, frozenset] | None = None):
        self.resolver = resolver
        self.timezone = timezone
        self.bridge_socket = bridge_socket
        self.write_commands = write_commands or {}
        self.schemas = MULTI_USER_TOOL_SCHEMAS
        self.task_status_schema = _task_status_schema(True)
        self._surfaces: OrderedDict[str, Any] = OrderedDict()
        self._tasks_by_user: dict[str, AnalysisTasks] = {}

    def _surface_for(self, database: Path):
        key = str(database)
        surface = self._surfaces.get(key)
        if surface is not None:
            self._surfaces.move_to_end(key)
            return surface
        from hermes_insights.local_surface import LocalSurface
        surface = LocalSurface(database=database, timezone=self.timezone)
        self._surfaces[key] = surface
        if len(self._surfaces) > MAX_CACHED_SURFACES:
            self._surfaces.popitem(last=False)
        return surface

    def _tasks_for(self, user: str, database: Path) -> AnalysisTasks:
        tasks = self._tasks_by_user.get(user)
        if tasks is None:
            tasks = AnalysisTasks(database, self.timezone)
            self._tasks_by_user[user] = tasks
        return tasks

    def resolve(self, arguments: dict):
        user = arguments.get("user")
        database = self.resolver.resolve(user)
        if database is None:
            return None
        rest = {key: value for key, value in arguments.items() if key != "user"}
        return self._surface_for(database), self._tasks_for(user, database), rest

    def health_write_request(self, arguments: dict):
        """Synchronous health_write execution (called via asyncio.to_thread).
        The user is resolved exactly like reads (same UserResolver, same
        panel.db active gate, fail closed); the request crosses the private
        Unix-socket broker as structured JSON and its outcome — refusals
        included — is returned verbatim. There is no second audit layer:
        the broker's handle() already audits every call."""
        if self.bridge_socket is None:
            return {"status": "refused", "error": NO_BROKER_REFUSAL}
        user = arguments.get("user")
        if not isinstance(user, str) or self.resolver.resolve(user) is None:
            raise ValueError("unknown user")
        payload, error = build_health_write_request(arguments, self.write_commands, user)
        if error is not None:
            raise ValueError(error)
        try:
            return broker_exchange(self.bridge_socket, payload)
        except OSError:
            raise ValueError("write broker unavailable")

    async def close(self):
        await asyncio.gather(*(tasks.close() for tasks in self._tasks_by_user.values()))


def create_server(target):
    # Import lazily: --help and the product itself do not need the optional SDK.
    from mcp import types
    from mcp.server import Server

    server = Server("OpenHealthAtlas Local", version="1.0.0", instructions=(
        "Read-only deterministic tools over the explicitly selected local dataset. "
        "In multi-user deployments with a configured write broker, health_write "
        "logs validated records per user through that audited broker. "
        "Discover features, query or analyze, then replay evidence_refs before explaining. "
        "Analysis and evidence return task receipts: poll health_task_status every five "
        "seconds until completed and use its result as a snapshot, not a freshness claim. "
        "Only one heavy task runs at a time; "
        "tasks expire ten minutes after completion and are lost when this connection closes. "
        "Preserve returned sources, dates, units, missingness and limitations. "
        "The connected client chooses its model and owns interpretation; this server makes "
        "no provider calls. Do not claim diagnosis or causation. Local transport does not "
        "prevent the client from sending tool results to its selected model provider."
    ))

    @server.list_tools()
    async def list_tools():
        tools = [types.Tool(
            name=name, description=TOOL_DESCRIPTIONS[name], inputSchema=schema,
            annotations=types.ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, openWorldHint=False,
            ),
        ) for name, schema in target.schemas.items()]
        tools.append(types.Tool(
            name="health_task_status",
            description=TASK_STATUS_DESCRIPTION,
            inputSchema=target.task_status_schema,
            annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        ))
        # Broker-mediated ingest is multi-user only and present in every
        # multi-user configuration; without --bridge-socket it refuses
        # clearly instead of disappearing (degraded, not broken).
        if target.multi_user:
            tools.append(types.Tool(
                name="health_write",
                description=HEALTH_WRITE_DESCRIPTION,
                inputSchema=HEALTH_WRITE_SCHEMA,
                annotations=types.ToolAnnotations(
                    readOnlyHint=False, destructiveHint=False, openWorldHint=False,
                ),
            ))
        return tools

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        # Closed validators own value validation. No SDK coercion or SDK
        # exception strings may replace that boundary. In multi-user mode,
        # `target.resolve` is also the ONLY place `user` is interpreted: it
        # never reaches the operation-specific validators below.
        try:
            encoded_request = json.dumps(arguments, allow_nan=False, separators=(",", ":"))
            if len(encoded_request.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
            if not isinstance(arguments, dict):
                raise ValueError("invalid arguments")
            if name == "health_write":
                # Never packaged as a read: no surface is created for the
                # target user — the broker runs the validated write under
                # its own child env. Unknown users raise the same opaque
                # refusal reads produce.
                result = await asyncio.to_thread(
                    target.health_write_request, arguments,
                )
                encoded = encode_result(result)
                if len(encoded.encode("utf-8")) > MAX_TOOL_BYTES:
                    raise ValueError("response too large")
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=encoded)],
                    structuredContent=json.loads(encoded),
                    isError=result.get("ok") is False or result.get("status") == "refused",
                )
            resolved = target.resolve(arguments)
            if resolved is None:
                raise ValueError("unknown user")
            surface, tasks, rest = resolved
            if name == "health_task_status":
                if (not {"task_id"} <= rest.keys() <= {"task_id", "wait_seconds"}
                        or not isinstance(rest["task_id"], str)
                        or len(rest["task_id"]) != 32
                        or type(rest.get("wait_seconds", 5)) is not int
                        or not 0 <= rest.get("wait_seconds", 5) <= 20):
                    raise ValueError("invalid task arguments")
                result = await tasks.wait(rest["task_id"], rest.get("wait_seconds", 5))
            else:
                if name not in TOOL_SCHEMAS:
                    raise ValueError("unavailable operation")
                request = {"operation": name, "arguments": {**DEFAULTS[name], **rest}}
                if name in {"health_analyze", "health_evidence"}:
                    result = tasks.submit(request)
                else:
                    result = await asyncio.to_thread(execute_request, surface, request)
            encoded = encode_result(result)
            if len(encoded.encode("utf-8")) > MAX_TOOL_BYTES:
                raise ValueError("response too large")
        except Exception:
            result = {"status": "refused", "error": "local evidence request failed closed"}
            encoded = encode_result(result)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=encoded)],
            structuredContent=json.loads(encoded),
            isError=(result.get("status") in {"refused", "failed"}
                     or (result.get("status") == "completed"
                         and result.get("result", {}).get("status") == "refused")),
        )

    return server


async def serve(target):
    from mcp.server.stdio import stdio_server

    server = create_server(target)
    loop = asyncio.get_running_loop()
    running = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, running.cancel)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await target.close()
        loop.remove_signal_handler(signal.SIGTERM)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--database", type=Path, help="Existing local health database (single-user mode)")
    mode.add_argument("--users-root", type=Path, help=(
        "Directory with one subdirectory per user, each containing that "
        "user's health.db (multi-user mode; see docs/MULTI_USER.md)"
    ))
    parser.add_argument("--owner-database", type=Path, help=(
        "Legacy single-user health.db mapped to the reserved 'owner' id; "
        "only meaningful with --users-root"
    ))
    parser.add_argument("--panel-db", type=Path, help=(
        "The panel's users table, opened strictly read-only, gating which "
        "users are active; only meaningful with --users-root"
    ))
    parser.add_argument("--bridge-socket", type=Path, help=(
        "The hermes-bridge broker's Unix socket, enabling the audited "
        "health_write ingest tool; only meaningful with --users-root"
    ))
    parser.add_argument("--timezone", default="UTC", help="Calendar timezone (default: UTC)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.users_root is None and (args.owner_database is not None or args.panel_db is not None):
        parser.error("--owner-database and --panel-db require --users-root")
    if args.bridge_socket is not None and args.users_root is None:
        # Writes are never direct-to-SQLite: without the multi-user broker
        # there is no ingest surface at all (single-user --database mode
        # stays read-only).
        parser.error("--bridge-socket requires --users-root")
    if args.worker:
        # Retain a deadline even if the owning stdio process is killed abruptly.
        signal.alarm(WORKER_TIMEOUT_SECONDS)
    try:
        if args.worker or args.database is not None:
            from hermes_insights.local_surface import LocalSurface
            surface = LocalSurface(database=args.database, timezone=args.timezone)
            if args.worker:
                raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
                if not raw or len(raw) > MAX_REQUEST_BYTES:
                    raise ValueError("invalid request size")
                sys.stdout.write(encode_result(execute_request(surface, json.loads(raw))))
                signal.alarm(0)
            else:
                target = SingleUserTarget(surface, AnalysisTasks(Path(surface.database), args.timezone))
                asyncio.run(serve(target))
        else:
            resolver = UserResolver(args.users_root, args.owner_database, args.panel_db)
            target = MultiUserTarget(
                resolver, args.timezone,
                bridge_socket=None if args.bridge_socket is None else str(args.bridge_socket),
                write_commands=load_write_commands(),
            )
            asyncio.run(serve(target))
    except (asyncio.CancelledError, KeyboardInterrupt):
        return 0
    except ImportError:
        print("Install requirements-mcp.txt in this Python environment.", file=sys.stderr)
        return 2
    except Exception:
        print("Local MCP startup failed; check the selected database, timezone and dependencies.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
