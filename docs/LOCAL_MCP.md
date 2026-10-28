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

### Granting the MCP read access (containerized deployment)

In the container deployment the data directories are owned by the container
uid (e.g. 1000) with mode 0700, so an MCP running as another host account
sees nothing until the owner grants a read ACL. Run once, as the directory
owner (no sudo needed), replacing `mcpuser` with the account that runs the MCP:

```
setfacl -m  u:mcpuser:--x  <data>  <state>              # traverse only
setfacl -R -m u:mcpuser:rX  <data>/users                # read all users
setfacl -d -m u:mcpuser:rX  <data>/users                # inherit for new users
setfacl -m  u:mcpuser:r--  <state>/panel.db             # --panel-db (disabled-user check)
```

New user directories are created with mode 0750, not 0700, on purpose: the
group bits are the POSIX-ACL *mask*, so 0700 would silently cancel the grant
for every user created afterwards. `panel.db` holds passkey public keys,
hashed session tokens and the audit log; granting it read access is what
lets the MCP refuse disabled users.

## Write/ingest (`health_write`)

By default the standalone MCP server is read-only. In multi-user mode the
operator MAY additionally configure the deployment's existing write broker
(the same Unix-socket broker the panel itself uses) so the connected AI
client — for example a family health coach — can LOG records on behalf of a
named family member: calories/food (`eat`, `log-food`), training sets
(`log-set`) and routines (`routine-set`, `routine-remove`, `routine-undo`),
check-ins and day ratings (`checkin`, `day-rating`), notes (`write-note`),
commitments (`commitment-set`, `commitment-list`, `log-commitment`),
batch/prep/schedule entries (`set-batch`, `prep`, `schedule-set`), and a full
multi-frequency BIA body-composition scan (`log-body-composition`) — e.g. an
agent that read a photo of a device printout like a Relaxmedic body
composition analysis report.

The panel's own guarded Hevy CSV import (`import-hevy-upload`, the Body
page's "Import Hevy CSV" button) is a separate, narrower exception reachable
only from the panel itself — it is deliberately absent from `health_write`
and every other collector-only Hevy command stays off this list too. See
PRODUCT_AUTHORITY.md "Fork deployment: guarded Hevy CSV import on the panel".

`log-body-composition` takes its whole report as one JSON document in
`stdin` (as a string, up to 64 KiB — larger than the 4 KiB free-text cap used
by `write-note`): `date` (required, `YYYY-MM-DD`), optional `tested_at`
(`HH:MM`), `device`, `subject_label`, `sex`, `age_years`, `height_cm`;
`components` (an array of `{component, value_kg, ref_low, ref_high,
pct_of_weight, evaluation}`, `component` one of `weight`, `body_fat`,
`inorganic_salt`, `protein`, `body_water`, `muscle`, `skeletal_muscle`);
`segments` (an array of `{kind, segment, mass_kg, pct_of_standard,
evaluation, std_low_pct, std_high_pct}`, `kind` one of `fat`/`muscle`,
`segment` one of `left_arm`, `right_arm`, `trunk`, `left_leg`, `right_leg`);
`impedance` (an array of `{frequency_khz, segment, ohms}`); and the scalar
obesity/score/control fields (`bmi`, `body_fat_pct`, `body_score`,
`target_weight_kg`, `weight_control_kg`, `fat_control_kg`,
`muscle_control_kg`, `bmi_band`, `body_fat_band`, `obesity_degree_pct`,
`obesity_degree_band`, `body_type`, `visceral_fat_grade`, `bmr_kcal`,
`fat_free_mass_kg`, `subcutaneous_fat_pct`, `smi_kg_m2`, `body_age`, `whr`,
`notes`, `report_ref`). Every field is validated server-side (closed schema,
plausibility-clamped, enum-checked); re-sending the same `(date, tested_at,
device)` replaces that scan instead of duplicating it. The scan's weight and
body-fat % are also mirrored into the ordinary weight/body-fat trend as a
`bia-scan`-sourced row, without shadowing a manual entry for the same date.
docs/PRODUCT_AUTHORITY.md "Fork deployment: body composition analysis
report" records the owner decision.

Nothing about the write path is new: every `health_write` call crosses the
hermes-bridge broker's SO_PEERCRED gate, subcommand/flag whitelist, stdin
rules and append-only audit log, exactly like a panel write. The MCP never
touches SQLite for a write and never writes a file itself; single-user
`--database` mode stays strictly read-only (the tool is absent there, and
`--bridge-socket` requires `--users-root`). Where no broker socket is
configured, the multi-user tool is present but refuses: "no broker
configured; ingest is unavailable in this deployment". It does NOT replace
the panel: the panel remains the human interface for browsing, correcting,
administering users and everything the broker does not whitelist (imports,
admin commands, collections, analysis jobs); `health_write` is logging only.

Configure it by adding one argument to the multi-user configuration above:

```json
"--bridge-socket", "/run/hermes-bridge/bridge.sock"
```

The broker must also authorize the MCP's host account in its peer-credential
gate. `BRIDGE_ALLOWED_UID` is a comma-separated set of trusted peer uids
(each a bare integer, no spaces or duplicates); one value keeps the
historical single-trusted-client behavior. The MCP process's running uid is
appended as a second trusted peer:

```
BRIDGE_ALLOWED_UID=1000,10000
```

Keep the socket ACL as narrow as the deployment requires — a group readable
only by the broker's own service group plus the panel and the extra client
accounts. Continuation of the read grants above:

```
setfacl -m u:mcpuser:--x /run/hermes-bridge                      # traverse the socket dir
setfacl -m u:mcpuser:rw- /run/hermes-bridge/bridge.sock          # connect to the broker socket
```

Run as the socket's owner (the broker's service account creates it mode 0660
group `hermesbridge` on every start); replace `mcpuser` with the account
that runs the MCP after the broker has started.

**Trust caveat for writes (stronger than for reads):** the connected MCP
client chooses `user` on every call — there is no login and no passkey here.
Whoever can talk through the coach can therefore write health data into any
user the deployment lets that socket reach: a wrong `user` name is a
recorded-but-real write into someone else's log. Deployment must keep the
socket ACL narrow and the broker's audit trail observed (`deploy/hermes-bridge`
audits every call, refused or accepted, with note contents hashed rather than
verbatim). The MCP returns the broker's outcome JSON — refusals included —
verbatim to the client; product validation (argparse choices, dates, values)
remains owned by health.py and the broker, never re-implemented client-side.

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
| `health_write` | **Multi-user only, ingest-only.** Logs a validated record (food/calories, training sets, routines, check-ins, day ratings, notes, commitments, schedules, or a full body-composition scan) for one named user through the deployment's audited write broker; present-but-refusing when no broker socket is configured. Not available in single-user mode. See [Write/ingest (health_write)](#writeingest-health_write). |

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
databases, provide raw SQL, open an arbitrary file or write health records
directly; in multi-user mode its only database-related choice is which
already-resolved `user` to act as. The canonical health database is opened
read-only. (`health_write`, when a broker socket is configured, writes only
through the audited broker — never into the database this server itself
opens; see [Write/ingest (health_write)](#writeingest-health_write).) Private
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
