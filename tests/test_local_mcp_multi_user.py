"""Unit tests for the standalone MCP's multi-user resolver.

These exercise `UserResolver` and the schema/dispatch helpers directly, with
no MCP SDK dependency, so they run even where requirements-mcp.txt is not
installed. The full real-protocol multi-user flows (cross-user isolation over
actual tool calls, task ids, evidence replay) live in test_local_mcp.py next
to the existing single-user protocol tests, guarded by the same
`pytest.importorskip("mcp")`.
"""

from __future__ import annotations

import runpy
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "openhealthatlas_mcp.py"


@pytest.fixture()
def mcp_module():
    return runpy.run_path(str(SERVER), run_name="local_mcp_multi_user_test")


def _users_root(tmp_path: Path) -> Path:
    users_root = tmp_path / "users"
    users_root.mkdir()
    (users_root / "alice").mkdir()
    (users_root / "alice" / "health.db").write_text("alice-fictional")
    return users_root


def _panel_db(tmp_path: Path, rows: dict[str, str]) -> Path:
    path = tmp_path / "panel.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    con.executemany("INSERT INTO users(id, status) VALUES (?, ?)", rows.items())
    con.commit()
    con.close()
    return path


def test_resolves_known_user_and_owner(mcp_module, tmp_path):
    users_root = _users_root(tmp_path)
    owner_db = tmp_path / "owner-health.db"
    owner_db.write_text("owner-fictional")
    resolver = mcp_module["UserResolver"](users_root, owner_db, None)

    assert resolver.resolve("alice") == users_root / "alice" / "health.db"
    assert resolver.resolve("owner") == owner_db


def test_refuses_unknown_malformed_and_escaping_ids(mcp_module, tmp_path):
    users_root = _users_root(tmp_path)
    resolver = mcp_module["UserResolver"](users_root, None, None)
    outside = tmp_path / "outside"
    outside.mkdir()
    (users_root / "evil").symlink_to(outside, target_is_directory=True)

    assert resolver.resolve("ghost") is None            # well-formed, unknown
    assert resolver.resolve("../etc") is None            # traversal: fails the id regex
    assert resolver.resolve("alice/x") is None           # "/" fails the id regex
    assert resolver.resolve("Alice") is None             # uppercase fails the id regex
    assert resolver.resolve("") is None                  # empty fails the id regex
    assert resolver.resolve(None) is None                # missing: never defaulted
    assert resolver.resolve(123) is None                 # wrong type
    assert resolver.resolve("evil") is None              # symlink escaping the root
    assert resolver.resolve("owner") is None             # no --owner-database given


def test_owner_unknown_without_owner_database_even_if_directory_exists(mcp_module, tmp_path):
    users_root = _users_root(tmp_path)
    (users_root / "owner").mkdir()
    (users_root / "owner" / "health.db").write_text("decoy")
    resolver = mcp_module["UserResolver"](users_root, None, None)

    # "owner" is special-cased to --owner-database, not a users-root subdirectory.
    assert resolver.resolve("owner") is None


def test_panel_db_gates_active_status_for_every_id_including_owner(mcp_module, tmp_path):
    users_root = _users_root(tmp_path)
    owner_db = tmp_path / "owner-health.db"
    owner_db.write_text("owner-fictional")
    panel_db = _panel_db(tmp_path, {"alice": "active", "owner": "active"})
    resolver = mcp_module["UserResolver"](users_root, owner_db, panel_db)

    assert resolver.resolve("alice") == users_root / "alice" / "health.db"
    assert resolver.resolve("owner") == owner_db

    (tmp_path / "disabled").mkdir()
    disabled_panel = _panel_db(tmp_path / "disabled", {"alice": "disabled", "owner": "active"})
    resolver_disabled = mcp_module["UserResolver"](users_root, owner_db, disabled_panel)
    assert resolver_disabled.resolve("alice") is None    # directory exists but panel says disabled

    (tmp_path / "no-owner-row").mkdir()
    no_owner_row_panel = _panel_db(tmp_path / "no-owner-row", {"alice": "active"})
    resolver_no_owner = mcp_module["UserResolver"](users_root, owner_db, no_owner_row_panel)
    assert resolver_no_owner.resolve("alice") == users_root / "alice" / "health.db"
    assert resolver_no_owner.resolve("owner") is None    # no panel row for owner: refused


def test_unreadable_panel_db_fails_closed_for_every_user(mcp_module, tmp_path):
    users_root = _users_root(tmp_path)
    owner_db = tmp_path / "owner-health.db"
    owner_db.write_text("owner-fictional")

    missing_panel = tmp_path / "does-not-exist.db"
    resolver_missing = mcp_module["UserResolver"](users_root, owner_db, missing_panel)
    assert resolver_missing.resolve("alice") is None
    assert resolver_missing.resolve("owner") is None

    corrupt_panel = tmp_path / "corrupt-panel.db"
    corrupt_panel.write_bytes(b"not a sqlite database")
    resolver_corrupt = mcp_module["UserResolver"](users_root, owner_db, corrupt_panel)
    assert resolver_corrupt.resolve("alice") is None
    assert resolver_corrupt.resolve("owner") is None

    no_table_panel = tmp_path / "no-table-panel.db"
    sqlite3.connect(no_table_panel).close()
    resolver_no_table = mcp_module["UserResolver"](users_root, owner_db, no_table_panel)
    assert resolver_no_table.resolve("alice") is None
    assert resolver_no_table.resolve("owner") is None


def test_panel_db_connection_is_opened_strictly_read_only(mcp_module, tmp_path):
    users_root = _users_root(tmp_path)
    panel_db = _panel_db(tmp_path, {"alice": "active"})
    before = panel_db.read_bytes()
    resolver = mcp_module["UserResolver"](users_root, None, panel_db)

    assert resolver.resolve("alice") is not None
    # The same mode=ro + query_only + deny-all-but-SELECT posture app/db_read.py
    # uses for panel.db: a write through that connection style must fail.
    con = sqlite3.connect(f"file:{panel_db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("UPDATE users SET status='disabled' WHERE id='alice'")
    con.close()
    assert panel_db.read_bytes() == before


def test_multi_user_tool_schemas_require_user_and_forbid_extras(mcp_module):
    multi_schemas = mcp_module["MULTI_USER_TOOL_SCHEMAS"]
    single_schemas = mcp_module["TOOL_SCHEMAS"]
    assert set(multi_schemas) == set(single_schemas)
    for name, schema in multi_schemas.items():
        assert schema["additionalProperties"] is False
        assert "user" in schema["required"]
        assert schema["properties"]["user"]["type"] == "string"
        # Multi-user mode adds exactly one key; nothing else moves.
        assert set(schema["properties"]) == set(single_schemas[name]["properties"]) | {"user"}
        assert "user" not in single_schemas[name]["properties"]

    single_status = mcp_module["_task_status_schema"](False)
    multi_status = mcp_module["_task_status_schema"](True)
    assert "user" not in single_status["required"]
    assert "user" in multi_status["required"]
    assert multi_status["additionalProperties"] is False


def test_multi_user_target_resolve_strips_user_and_isolates_per_user_state(mcp_module, tmp_path):
    users_root = tmp_path / "users"
    users_root.mkdir()
    for name in ("alice", "bob"):
        (users_root / name).mkdir()
        (users_root / name / "health.db").write_text(f"{name}-fictional")
    resolver = mcp_module["UserResolver"](users_root, None, None)
    target = mcp_module["MultiUserTarget"](resolver, "UTC")

    resolved_alice = target.resolve({"user": "alice", "search": "sleep"})
    assert resolved_alice is not None
    surface_a, tasks_a, rest_a = resolved_alice
    assert rest_a == {"search": "sleep"}
    assert str(surface_a.database).endswith("alice/health.db")

    resolved_bob = target.resolve({"user": "bob", "search": "sleep"})
    surface_b, tasks_b, rest_b = resolved_bob
    assert str(surface_b.database).endswith("bob/health.db")

    assert surface_a is not surface_b
    assert tasks_a is not tasks_b
    # A second call for the same user reuses the cached surface and the same
    # per-user task registry instead of constructing new ones each time.
    surface_a_again, tasks_a_again, _ = target.resolve({"user": "alice", "search": "x"})
    assert surface_a_again is surface_a
    assert tasks_a_again is tasks_a

    assert target.resolve({"search": "sleep"}) is None       # no 'user' key at all
    assert target.resolve({"user": "ghost"}) is None          # unknown user
