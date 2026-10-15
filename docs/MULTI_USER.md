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
- Multi-user passkey enrollment and login; sessions bound to a user; disabled users cannot sign in and their sessions stop working.
- Per-user health DB initialisation (reuse `scripts/init_hermes.py` logic).
- Cross-user isolation tests: user A can never read, write, export, or poll jobs/chat of user B.

Out (single owner only, **fail closed** for non-bootstrap users with a clear message):

- Hermes chat and agent features, Telegram, scheduled collectors (Hevy, Google Health), the stdio MCP server, the macOS desktop adapter.
- Per-user timezone, medication and nutrition profile (`HERMES_*` settings stay installation-wide).
- Admin web UI.

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
`<USERS_ROOT>/<id>/` (mode 0700) and initializes `<id>/health.db` +
`<id>/vault` exactly like `scripts/init_hermes.py` does for a fresh install —
nothing is shared with any other user's data. The existing single-user
install becomes the bootstrap `owner` automatically (on first legacy-row
migration, or lazily on first `flask enroll-token` with no `--user`); it keeps
its original `HEALTH_DB`/`VAULT_DIR` unchanged.

### Panel configuration

| Variable | Default | Purpose |
|---|---|---|
| `USERS_ROOT` | `<HERMES_DATA_DIR>/users` | Where per-user `health.db`/`vault` directories live. |

### Broker configuration (`deploy/hermes-bridge`)

| Variable | Default | Purpose |
|---|---|---|
| `BRIDGE_USERS_ROOT` | unset | Enables multi-user mode in the broker. Unset: the broker ignores `user` entirely and behaves exactly as a single-user install always has. Set: every request must name a real, active identity — the bootstrap owner (`BRIDGE_OWNER_USER`) or a subdirectory of this root — or the broker refuses with code 2 and audits the refusal. |
| `BRIDGE_OWNER_USER` | `owner` | The id that maps to the broker's own configured `HEALTH_DB`/`HEALTH_VAULT` unchanged (no per-user override). |

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
