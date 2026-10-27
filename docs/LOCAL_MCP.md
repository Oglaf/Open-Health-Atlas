# Local MCP setup

The optional OpenHealthAtlas MCP server lets a compatible AI client inspect
and analyse an existing local health database. Choose the model in your client.
The server supplies deterministic records and evidence, and makes no model or
provider calls itself.

The [macOS desktop preview](DESKTOP_MCP.md) bundles this server and exports
client configuration from Desktop Help. Its publication and platform limits
are recorded in [desktop acceptance](DESKTOP_ACCEPTANCE.md). The source setup
below remains supported independently.

## Install and create a fictional database

Use Git and Python 3.11 or newer on macOS or Linux. The current analytical
engine uses POSIX file locks; native Windows execution is not supported by
this entry point. A client's support for launching a server in a separate
Linux environment depends on that client.

Run this in one terminal. If you already cloned the repository, start from
its root and skip the first two lines. Dependency installation needs internet
access; generating and querying these fictional records does not.

```bash
git clone https://github.com/kajeesan/Open-Health-Atlas.git
cd Open-Health-Atlas
oha_mcp_env="$(mktemp -d)/venv"
python3 -m venv "$oha_mcp_env"
"$oha_mcp_env/bin/python" -m pip install -r requirements-mcp.txt
export PYTHONDONTWRITEBYTECODE=1
export HERMES_TIMEZONE=UTC
unset HERMES_HEVY_QUARTERLY_CONFIG
oha_mcp_data="$(mktemp -d)"
"$oha_mcp_env/bin/python" scripts/make_demo_db.py \
  --output "$oha_mcp_data/health-demo.db" --anchor-date 2026-06-30
printf 'Python: %s\nServer: %s\nDatabase: %s\n' \
  "$oha_mcp_env/bin/python" "$PWD/scripts/openhealthatlas_mcp.py" \
  "$oha_mcp_data/health-demo.db"
```

Use the three printed absolute paths in the configuration below. Temporary
paths are for this trial; move to a persistent external environment and data
directory for ongoing use, then update the client configuration.

To use your own data later, select an existing OpenHealthAtlas database or
follow the [initialization and import guidance](DEVELOPMENT.md#verify-the-setup).
The MCP server does not initialize, migrate or import a database. An arbitrary
CSV, PDF or SQLite file is not an OpenHealthAtlas database; use a supported
import format and inspect its CLI help first.

## Connect your client

Add a local **stdio** MCP server to your AI client. For clients that accept
the common `mcpServers` configuration format, replace the paths below with
absolute paths on your own computer:

```json
{
  "mcpServers": {
    "openhealthatlas": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": [
        "-B",
        "/absolute/path/to/Open-Health-Atlas/scripts/openhealthatlas_mcp.py",
        "--database", "/absolute/path/to/health-demo.db",
        "--timezone", "UTC"
      ]
    }
  }
}
```

Choose the IANA timezone that defines your data's calendar days. The default is
`UTC`. The client starts the MCP process and communicates over its standard
input/output; no web server or network listener is started. Provider
credentials belong in the AI client, not this configuration.

Clients that only accept remote MCP connections cannot use this stdio
configuration directly. Any remote transport requires a separate setup; this
guide verifies only the local server. The server itself makes no model calls,
but your client may send returned records to its configured model provider.
A local database is not a guarantee that the client processes results locally.

## Multi-user mode

A household or small group covered by one [multi-user install](MULTI_USER.md)
can use one Hermes profile (one MCP entry) for everyone, instead of one
profile per person. Start the same server with `--users-root` instead of
`--database`; every tool call then names which person's data to use:

```json
{
  "mcpServers": {
    "openhealthatlas": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": [
        "-B",
        "/absolute/path/to/Open-Health-Atlas/scripts/openhealthatlas_mcp.py",
        "--users-root", "/opt/docker/medical-oha-data/users",
        "--owner-database", "/opt/docker/medical-oha-data/health.db",
        "--panel-db", "/opt/docker/medical-oha-state/panel.db",
        "--timezone", "UTC"
      ]
    }
  }
}
```

`--database` and `--users-root` are mutually exclusive; pick exactly one.
`--users-root` must contain one subdirectory per person, each holding that
person's `health.db` (the same layout the panel and broker already use under
`USERS_ROOT`/`BRIDGE_USERS_ROOT`; see [docs/MULTI_USER.md](MULTI_USER.md)).
`--owner-database` is optional and maps the reserved id `owner` to the
pre-existing single-user `health.db` that lives outside `--users-root`
(the bootstrap identity from a single-user install); without it, `owner` is
simply an unknown user. `--panel-db` is optional and points at the panel's
own `panel.db`, opened strictly read-only (the same `mode=ro` + `query_only`
+ deny-all-but-`SELECT` posture `app/db_read.py` uses); when given, a name
must have an `active` row in that database's `users` table — the owner
included — or the request is refused exactly like an unknown user, and a
`panel.db` that cannot be read refuses every name rather than allowing one.
Without `--panel-db`, the only check is that the person's directory exists;
a person disabled through the panel's admin tooling is not enforced at this
layer, only at the panel/broker layer, until `--panel-db` is also configured.

In multi-user mode, every tool call takes one additional required argument:

```json
{"user": "alex", "feature_keys": ["sleep.duration_hours"], "range": {"from": "2026-06-24", "to": "2026-06-30"}, "view": "summary"}
```

`user` is a lookup key, never a path: it must match `[a-z0-9][a-z0-9_-]{0,31}`,
and the server resolves it itself against `--users-root`/`--owner-database`/
`--panel-db`. The connected client cannot select or discover a database path
through any argument. A missing, malformed, unknown or disabled `user`
produces the same opaque refusal as any other failed request — it never
reveals which check failed or any filesystem path. Each resolved database
gets its own isolated per-user state (surface, background analysis tasks,
task ids): a task id or evidence reference minted for one person is refused,
not honored, under a different `user`. All users still share the single
`--timezone` configured at startup; per-user timezone remains out of scope
(see [docs/MULTI_USER.md](MULTI_USER.md) Phase 2).

**This is not an authorization boundary between family members.** The MCP
client — not a login, not a passkey — chooses `user` on every call, so access
here is trusted to whoever can launch or talk to this process at all, the
same way a shared Hermes profile is trusted to whoever has it configured.
The real boundary is file permissions: `--users-root` and `--panel-db` must
only be readable by the account running this MCP server, exactly as the
panel/broker's own bind-mounts restrict access in [docs/MULTI_USER.md](MULTI_USER.md#systemd-bind-mount-implications).
If a family member should not be able to read another member's records
through this server, they must not share the account or process that runs it.

## First use

After connecting, check that the client lists the tools below. Start with a
question such as “Which sleep and training measurements are available?” Then
ask about a specific date range present in your records.

| Tool | Purpose |
| --- | --- |
| `health_catalog` | Discover available registered features, their units and supported operations. |
| `health_query` | Read latest values, daily series, summaries or a period comparison. |
| `health_analyze` | Start a bounded analysis for an outcome and selected exposures. |
| `health_evidence` | Start verification of complete evidence references returned by earlier results. |
| `health_task_status` | Wait briefly for a background task and retrieve its result. |

The client should discover feature keys before querying, use explicit date
ranges and pass complete returned evidence-reference objects when verifying
an answer. Returned records preserve source, time, units, missingness and
calculation limitations. The model supplies the interpretation.

### Concrete trial requests

For the fictional database above, ask your client to use these tool calls in
order. Clients expose tool invocation differently; these are tool names and
argument objects, not text to paste into a shell.

1. Discover sleep features with `health_catalog`:

   ```json
   {"search": "sleep", "limit": 5}
   ```

2. Read the final week with `health_query`:

   ```json
   {"feature_keys": ["sleep.duration_hours"], "range": {"from": "2026-06-24", "to": "2026-06-30"}, "view": "summary"}
   ```

   Expect an `ok` result with facts, units and `evidence_refs`.

3. Request the retained full-range example with `health_analyze`:

   ```json
   {"outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"], "range": {"from": "2026-03-02", "to": "2026-06-30"}, "mode": "ordinal", "top": 1}
   ```

   Pass the returned `task_id` to `health_task_status` with `wait_seconds: 5`
   until it completes. The bundled fixture has a successful result with
   findings; a shorter or sparse range may correctly return `insufficient_data`.

4. Call `health_evidence` with an `evidence_refs` array containing a complete
   reference object from the query or completed analysis. Poll its task in the
   same way. Do not substitute a hash or a text summary for the object.

The [existing SDK tests](DEVELOPMENT.md#run-the-right-checks) exercise discovery,
query, positive analysis, evidence verification and refusals without a model.
A model client's own tool selection and final wording require separate review.

## Long analyses

Analysis and evidence verification return a task identifier promptly. The
client checks `health_task_status` until it receives the completed result or
an error. Status supports a bounded wait to avoid rapid polling. One heavy
task runs at a time; repeated submissions of the same pending request reuse
that task. A different heavy request receives a busy response and can be
submitted again after the current task finishes. In multi-user mode this
limit, and the task registry itself, are per `user`: one person's heavy
analysis does not block another's, and a task id is only ever visible under
the `user` it was started for.

Task handles belong to the running MCP process. If the client restarts it,
submit the request again. Exact analytical caches can still be reused when
their database, code, request and configuration match. Completed task results
are snapshots from their calculation; submit a fresh request or verify its
evidence when you need to check for changed records.

The server retains up to eight completed task results for ten minutes. A worker
has a 570-second execution limit. Normal client disconnect shuts down its
worker; if the server is terminated abruptly, the worker's own execution limit
still applies.

## Data and existing workflows

The database (or, in multi-user mode, the set of per-user databases under
`--users-root`) is selected at startup. A tool request cannot switch
databases, provide raw SQL, open an arbitrary file or write health records;
in multi-user mode its only database-related choice is which already-resolved
`user` to act as. The canonical health database is opened read-only. Private
derived cache and lock files may be created beside it; they are not
additional canonical health records.

Each database has an opaque dataset identity. Evidence from another configured
database, or from the separate fictional Hermes adapter, cannot be reused as
if it belonged to this connection. Moving the database to another path changes
that identity; ask for fresh evidence after moving it. In multi-user mode this
also means evidence from one `user` cannot be replayed as another's: the
dataset identity differs, so the server refuses the replay as stale rather
than silently returning someone else's data.

Existing panel, import, manual-entry, Hermes and Telegram paths continue to
use their own entry points. The standalone MCP server does not install Hermes,
change service configuration, enable schedules, or add built-in API-key chat.
The client's normal provider settings determine where returned tool results
are processed.
