# Multi-user design

Status: Phase 1 implemented on `feature/multi-user`.

OpenHealthAtlas is a single-owner product: one passkey identity, one `health.db`,
one vault. This design lets one installation serve several people (a household
or a small group) without weakening the existing privacy boundaries.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Data isolation | **One `health.db` and one vault per user**, under `<HERMES_DATA_DIR>/users/<user_id>/` | `toolkit/health.py` and `runtime.py` bind the database from `HEALTH_DB` at import time. A per-user file needs no schema migration of the v1–v7 health schema, keeps append-only protections intact, and makes a cross-user read structurally impossible rather than a `WHERE user_id=?` discipline problem. Backup, export and deletion are per-file. |
| Panel state | One shared `panel.db` with a `users` table; `user_id` on credentials, sessions, enroll tokens, chat and audit rows | Panel state is small and already separate from health records. |
| Authentication | Passkeys stay the only factor. An admin issues a one-time enrollment link **per user**; login is discoverable (usernameless) or restricted to a typed handle | Keeps the existing, audited WebAuthn flow. |
| Roles | `admin` (manages users, sees no one else's health data) and `member` | Admin rights are about accounts, not records. |
| Existing installs | The current data becomes a bootstrap user (`owner`, admin) pointing at the existing `HEALTH_DB`/`VAULT_DIR`. No file moves. | Upgrade must be non-destructive and reversible. |
| Writes | The panel sends a validated `user` id with each bridge request; the broker maps it to a path under its own configured users root and sets `HEALTH_DB`/`HEALTH_VAULT` for the `health.py` child. The panel never sends a path. | Same trust model as today: the broker owns privileged resolution. |

## Phase 1 scope (this branch)

In:

- `users` table, `user_id` columns, idempotent migration of existing rows to the bootstrap user.
- Per-request user on `g`; `db_read.get_health_db()`, `snapshot()`, vault lookups resolve per user.
- Bridge protocol carries `user`; broker resolves and validates it; audit records include it.
- Admin CLI: `flask user-create`, `user-list`, `user-disable`, `enroll-token --user`.
- Admin web UI (`/admin`, admin role only): add/manage family members without
  the CLI — see "Admin UI" under Operator guide below.
- Multi-user passkey enrollment and login; sessions bound to a user; disabled users cannot sign in and their sessions stop working.
- Per-user health DB initialisation (reuse `scripts/init_hermes.py` logic).
- Cross-user isolation tests: user A can never read, write, export, or poll jobs/chat of user B.
- The standalone stdio MCP server (`scripts/openhealthatlas_mcp.py`): an
  optional `--users-root` mode lets one MCP entry (one Hermes profile) serve
  every member of the family. See
  [docs/LOCAL_MCP.md "Multi-user mode"](LOCAL_MCP.md#multi-user-mode) for
  configuration and [tests/test_local_mcp.py](../tests/test_local_mcp.py) /
  [tests/test_local_mcp_multi_user.py](../tests/test_local_mcp_multi_user.py)
  for the acceptance tests. In this mode the MCP client selects `user` on
  every call, so (unlike the panel/broker) this is not an authorization
  boundary between family members — it is trusted-local to whoever can reach
  the process, the same trust level as a shared Hermes profile. File
  permissions on `--users-root`/`--panel-db` are the real boundary.
- Optional broker-mediated ingest (`health_write`): with
  `--bridge-socket` configured (it requires `--users-root`), the MCP client
  can LOG validated records (food/calories, training sets, routines,
  check-ins, day ratings, notes, commitments, schedules) for a named user
  through the same audited broker the panel uses — the MCP never writes
  SQLite directly. Without it, this MCP is read-only by default; ingest only
  where a broker socket is configured. See
  [docs/LOCAL_MCP.md "Write/ingest (health_write)"](LOCAL_MCP.md#writeingest-health_write).

Out (single owner only, **fail closed** for non-bootstrap users with a clear message):

- Hermes chat and agent features, Telegram, scheduled collectors (Hevy, Google Health), the macOS desktop adapter.
- Per-user timezone (including for the stdio MCP's `--users-root` mode, which
  keeps one `--timezone` for every served user) — `HERMES_TIMEZONE` stays
  installation-wide even where other `HERMES_*` settings now vary per user
  (see `deploy/bridge_commands.py`'s `PROFILE_KEYS`).

## Phase 2

Per-user timezone and profile via per-request environment for the broker child; per-user chat sessions; per-user collector credentials and timers; admin UI; per-user export/delete-account.

## Invariants to test

1. No route or helper accepts a user id, path or database name from the request.
2. Every `bridge.run` call carries the session user; a missing user is an error, not a default.
3. The broker rejects a user id that is not `[a-z0-9][a-z0-9_-]{0,31}` or whose resolved path escapes the users root.
4. Analysis-job ids, conversations, and cached analysis results are namespaced by user.
5. Single-user installs behave exactly as before (existing suite passes unchanged).

See `tests/test_multi_user.py` for the acceptance tests pinning these invariants
(cross-user isolation over the dashboard/export/vault-backed routes, disabled
sessions, the bridge/broker user resolution and traversal/symlink refusals,
the CLI lifecycle, and the legacy panel.db migration).

## Operator guide

### Creating a user

```
flask user-create <id> [--name "Display Name"] [--role admin|member]
flask user-list
flask user-disable <id>   # revokes that user's sessions immediately
flask user-enable <id>
flask enroll-token --user <id>   # prints a 15-minute, single-use enrollment URL
```

`<id>` must match `[a-z0-9][a-z0-9_-]{0,31}`. `user-create` makes
`<USERS_ROOT>/<id>/` (mode 0750 — group bits are the POSIX-ACL mask, so 0700 would cancel an operator read grant for the MCP) and initializes `<id>/health.db` +
`<id>/vault` exactly like `scripts/init_hermes.py` does for a fresh install —
nothing is shared with any other user's data. The existing single-user
install becomes the bootstrap `owner` automatically (on first legacy-row
migration, or lazily on first `flask enroll-token` with no `--user`); it keeps
its original `HEALTH_DB`/`VAULT_DIR` unchanged.

### Admin UI

`/admin` (admin role only) lets an admin add and manage family members from
the browser — no CLI or assistant required: a users table (role, status,
created, whether a custom profile is set), a create-user form, a one-time
"Generate enrollment link" button per user, enable/disable, role change, and
the per-user profile settings below. Guards enforced server-side (not just in
the UI): `owner` is never creatable, disable-able, or role-changeable; an
admin can never disable or demote themselves; the last active admin can never
be disabled or demoted.

Every mutation still crosses the broker exactly like every other panel write
— the admin page never opens a user's directory itself (the panel container
mounts the data volume read-only). Three admin-only broker subcommands back
it, declared in `deploy/bridge_commands.py` next to the existing allowlist:

| Subcommand | Does |
|---|---|
| `user-provision` | Creates `<BRIDGE_USERS_ROOT>/<id>/` (mode 0750, see MCP note in LOCAL_MCP.md) and initializes `health.db`/`vault` with the same code `scripts/init_hermes.py` uses. Idempotent (an already-initialized user is success, never overwritten). |
| `user-profile-write` | Validates and atomically writes that user's `profile.json` (stdin carries the JSON; whitelist is `deploy/bridge_commands.py`'s `PROFILE_KEYS`, the same one `flask user-profile` and the broker's own child-env overlay use). |
| `user-profile-clear` | Removes `profile.json` ("reset to defaults"). |

All three run in the broker process itself (never health.py or hermesctl —
there is no `HEALTH_DB` to point at before a user exists), refuse the `owner`
id and anything outside `BRIDGE_USERS_ROOT`, and refuse outright when
`BRIDGE_USERS_ROOT` is unset (single-user mode). `user-provision` imports
`scripts.init_hermes.init_health_database` directly, so it needs the full
application tree next to the broker — true for the Docker image
([docs/DOCKER.md](DOCKER.md), one shared image for panel/broker/admin) but
**not** for the minimal systemd install (`deploy/install-bridge.sh` copies
only `hermes-bridge`, `bridge_commands.py` and `hermesctl`); on that install
`user-provision` fails closed with a clear error instead of a crash, and
account creation falls back to `flask user-create` on the panel host.

### Self-service Profile & targets

`/profile` (any logged-in user, their own data only — see the 2026-10-29
PRODUCT_AUTHORITY.md entry) lets a user set their own T43/T44/§4b
configuration from the browser instead of the CLI/SSH/MCP-only path: sex,
date of birth, height, activity fallback (`profile-set`), diet phase
(`phase-set`), kcal/protein overrides (`nutrition-target-set`), and planned
wake/bed/workout/medication times (`planned-time-set`). These four
`health.py` subcommands already validated everything; this fork only added
them to the panel bridge's and the local MCP's allowlists
(`deploy/bridge_commands.py` COMMAND_FLAGS / `scripts/openhealthatlas_mcp.py`
HEALTH_WRITE_COMMANDS) — no new validation, no schema change.

The page also edits a non-owner user's own **per-user profile settings**
(above): micronutrient targets, diet-phase kcal offsets, and protein-per-kg,
all stored in that user's `profile.json`. Unlike the admin-only
`user-profile-write` (whole-file replace, admin picks the target id), this
goes through a new self-service broker command:

| Subcommand | Does |
|---|---|
| `user-profile-merge` | Merges a partial JSON patch (stdin) into the CALLING user's OWN `profile.json` — never a target argument. A key mapped to JSON null removes it. Re-validates the full merged document, including value-level checks (nutrient names against the known catalog, exact cut/maintain/bulk phase keys, numeric bounds) that catch a malformed value before it can take that user's own `health.py` child process down at import. Refused for the bootstrap owner (who has no `profile.json`) and for any id outside `BRIDGE_USERS_ROOT`. |

`user-profile-merge` runs in the broker process itself, like the admin
commands, but is declared separately (`SELF_SERVICE_COMMANDS`, not
`ADMIN_COMMANDS`) because its authorization model is different: there is no
separate "target id" to validate against an acting admin, since a user can
only ever merge their own file. The bootstrap owner has no `profile.json` at
all — it keeps reading the three nutrition `HERMES_*_JSON` variables from the
installation environment, so the page shows that section as unavailable with
a reason, while the DB-backed sections (profile, phase, targets, planned
times) still work for the owner too.

### Panel configuration

| Variable | Default | Purpose |
|---|---|---|
| `USERS_ROOT` | `<HERMES_DATA_DIR>/users` | Where per-user `health.db`/`vault` directories live. |

### Broker configuration (`deploy/hermes-bridge`)

| Variable | Default | Purpose |
|---|---|---|
| `BRIDGE_USERS_ROOT` | unset | Enables multi-user mode in the broker. Unset: the broker ignores `user` entirely and behaves exactly as a single-user install always has. Set: every request must name a real, active identity — the bootstrap owner (`BRIDGE_OWNER_USER`) or a subdirectory of this root — or the broker refuses with code 2 and audits the refusal. |
| `BRIDGE_OWNER_USER` | `owner` | The id that maps to the broker's own configured `HEALTH_DB`/`HEALTH_VAULT` unchanged (no per-user override). |
| `BRIDGE_ALLOWED_UID` | unset | The broker's SO_PEERCRED gate. A single bare integer is exactly the historical single trusted peer (the panel). A comma-separated set — e.g. `1000,10000` — authorizes the panel plus additional trusted clients (such as an MCP `health_write` ingest peer): bare integers, no spaces, no duplicates; anything else fails broker startup. |

`BRIDGE_USERS_ROOT` should normally equal the panel's `USERS_ROOT` (the panel
resolves and validates `<id>` client-side too — the broker is the
authoritative check, not the only one).

### systemd bind-mount implications

`deploy/hermes-panel.service` currently bind-mounts the single owner database
and vault read-only:

```
BindReadOnlyPaths=/var/lib/hermes/health.db /var/lib/hermes/vault
```

A multi-user panel needs read-only access to every user's `health.db`/`vault`,
not just the owner's — bind-mount the whole users root instead:

```
BindReadOnlyPaths=/var/lib/hermes/users
```

The panel only ever opens the current request's user's files under that
mount (see `app/db_read.py`, `app/users.py`); it never receives write access
regardless. The broker (`deploy/hermes-bridge.service`), which performs the
actual writes, needs read-write access to the same tree
(`ReadWritePaths=/var/lib/hermes`) and `BRIDGE_USERS_ROOT=/var/lib/hermes/users`
in its environment file. Neither unit file is changed by Phase 1 itself —
apply these two edits only when turning multi-user mode on for a real
deployment.
