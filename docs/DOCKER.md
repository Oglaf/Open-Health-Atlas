# Docker deployment

This describes running OpenHealthAtlas as containers instead of the systemd
units in [DEPLOYMENT.md](DEPLOYMENT.md). It preserves the same privilege split
described in [ARCHITECTURE.md](ARCHITECTURE.md#runtime-shape): the panel never
holds a write path into the health database, and every mutation crosses the
hermes-bridge broker over a Unix socket. Read [MULTI_USER.md](MULTI_USER.md)
first if you intend to serve more than one person.

## Image

One image, built from the repository root `Dockerfile`, runs as three
container roles selected by the command passed to the entrypoint:

| Role | Command | Role in the architecture |
|---|---|---|
| `panel` | `oha panel` | gunicorn serving the Flask app, read-only on health data |
| `broker` | `oha broker` | hermes-bridge, the only process that writes `health.db`/vault |
| `admin` | `oha init` / `oha user-create ...` / `oha flask ...` | one-off database/user management, never left running |

All three share one non-root uid/gid (1000) baked into the image. The
privilege boundary between the panel and the broker comes from which volumes
each container mounts read-only vs read-write (see below) — not from
different uids, since containers already isolate the two processes. The
broker is told to accept that uid directly (`BRIDGE_ALLOWED_UID=1000`) rather
than resolving a `panel` system account through `pwd`, which doesn't exist in
this image.

Build locally:

```bash
docker build -t open-health-atlas:local .
```

Or pull the published image:

```bash
docker pull ghcr.io/oglaf/open-health-atlas:multi-user
```

Tags: `multi-user` tracks the `feature/multi-user` branch, `latest` tracks
`main`, and `vX.Y.Z` tags a release once one is cut. Prefer a pinned `vX.Y.Z`
tag for anything other than local evaluation.

## First run

```bash
cd deploy/docker
cp .env.example .env
# edit .env: PANEL_SECRET_KEY, PANEL_RP_ID, PANEL_ORIGIN at minimum

docker network create proxy   # if your reverse proxy doesn't already own one
docker compose run --rm admin init
docker compose run --rm admin user-create alice --name "Alice" --role admin
docker compose run --rm admin enroll-token --user alice

docker compose up -d
```

`admin init` is idempotent and never overwrites an existing database — safe
to run again after an upgrade. The existing single-user install becomes the
bootstrap `owner` automatically on first `enroll-token` call with no
`--user`; `init` alone does not create it. Open the printed enrollment URL
(valid 15 minutes, single use) on the device you want to enroll a passkey on.

The `admin` service has `profiles: ["admin"]` in `docker-compose.yml`, so
`docker compose up` never starts it — only `docker compose run --rm admin
<command>` does.

## Volumes and who may write what

| Volume | Mounted at | panel | broker | admin |
|---|---|---|---|---|
| `oha-data` | `/data` (`health.db`, `users/`, vault) | read-only | read-write | read-write |
| `oha-state` | `/state` (`panel.db`) | read-write | — | read-write |
| `oha-socket` | `/sock` (the bridge Unix socket) | read-write | read-write | — |
| `oha-audit` | `/audit` (the broker's append-only audit log) | — | read-write | — |

The panel opens every user's `health.db` read-only (SQLite `mode=ro` plus an
in-process authorizer — see `app/db_read.py`); it cannot write `/data` even if
something in the application layer tried, because the mount itself is
read-only at the kernel level. Every write goes panel → `/sock` → broker →
`/data`. The broker's audit log at `/audit/bridge.jsonl` is append-only and
the panel container never mounts it.

Both long-lived containers run with `read_only: true` (an immutable
container filesystem) and a `tmpfs` at `/tmp` for the handful of things that
need scratch space — SQLite snapshot exports in the panel
(`app/routes/export.py`), and whatever `health.py` needs transiently in the
broker. Everything durable lives in the four named volumes above.

## Reverse proxy and HTTPS (mandatory for passkeys)

The panel binds `0.0.0.0:8100` inside its container and is not published to
the host by default — reach it through a reverse proxy on the `proxy` Docker
network (join your proxy's compose stack to that network, or run
`docker network create proxy` first if nothing else has). WebAuthn passkeys
are bound to the origin they were created under, so:

- `PANEL_ORIGIN` must equal the exact public `https://` origin users will
  authenticate through.
- `PANEL_RP_ID` must equal that origin's hostname.
- Set both correctly **before the first enrolment** — changing them later
  strands every passkey already created.
- `PANEL_COOKIE_SECURE=1` (the default) requires the proxy to terminate TLS
  and forward `X-Forwarded-Proto: https`; the app trusts exactly one
  forwarded-protocol hop (see `app/__init__.py`).

For a quick local smoke test without a reverse proxy, uncomment the
`127.0.0.1:5111:8100` port mapping on the `panel` service in
`docker-compose.yml` and set `PANEL_COOKIE_SECURE=0` / `PANEL_ORIGIN=http://localhost:5111`
— passkeys still require a secure context, so `localhost` is the only
plain-http origin that works for WebAuthn testing.

## Backups

Back up, per user, the two SQLite files and the vault directory:

- `/data/health.db` (owner) and `/data/users/<id>/health.db` (everyone else)
- `/data/vault` (owner) and `/data/users/<id>/vault`
- `/state/panel.db` (sessions, credentials, users table, audit log — shared)

Use SQLite's online backup API rather than copying the file directly, since a
plain copy can catch a database mid-write. The image has no `sqlite3` command-line
tool, so run the backup from Python in a throwaway container that mounts the
data read-only and a backup directory read-write:

```bash
docker run --rm --user 1000:1000 --network none --read-only --tmpfs /tmp \
  -v <data>:/data:ro -v <state>:/state:ro -v <backup>:/backup \
  --entrypoint python3 <image> -c "
import sqlite3
for src, dst in (('/data/health.db', '/backup/health.db'),
                 ('/state/panel.db', '/backup/panel.db')):
    s = sqlite3.connect(f'file:{src}?mode=ro', uri=True); t = sqlite3.connect(dst)
    with t: s.backup(t)
    print(dst, t.execute('PRAGMA quick_check').fetchone()[0])"
```

Repeat for each `/data/users/<id>/health.db`. Verify a restored copy opens and
passes `PRAGMA quick_check` before trusting it, per [DEPLOYMENT.md](DEPLOYMENT.md).

## Upgrades

```bash
docker compose pull
# Only if a release's notes say a schema migration is needed — back up first:
docker compose run --rm --entrypoint python3 admin toolkit/health.py schema-migrate
docker compose up -d
```

Migrations are forward-only; never overwrite the only copy of a database
before confirming a migration succeeded (see
[DEPLOYMENT.md's migration guidance](DEPLOYMENT.md#analysis-request-budget)).

## Scheduled wearable collector

Fork owner decision, 2026-10-28 (see
[PRODUCT_AUTHORITY.md](PRODUCT_AUTHORITY.md)): an optional `ghealth` compose
service runs the Google Health collector (`deploy/ghealth-sync`) as a
container instead of the root systemd timer described in
[DEPLOYMENT.md](DEPLOYMENT.md). It is profile-gated
(`profiles: ["ghealth"]`) — `docker compose up` never starts it, and starting
it is a separate, explicit decision per installation. It is a single-account
collector: one container writes into exactly one configured `HEALTH_DB`
(the owner's `health.db`, or one member's `/data/users/<id>/health.db`), the
same one-user-per-run shape as the systemd collector. Weight lands in
`body_metrics` with `source='fitbit'`; sleep, steps, active energy and the
daily physiology metrics land the same way the systemd collector already
writes them. The dashboard/engine source order (manual > fitbit > BIA scan >
Hevy) already ranks a Fitbit-sourced weight correctly against the other
writers.

To set it up:

1. Create `deploy/docker/ghealth.json` from
   `deploy/docker/ghealth.json.example` — either by running
   `deploy/ghealth-auth` (see its own docstring; it needs a browser and an SSH
   tunnel if run on a headless host) and copying the resulting
   `/etc/hermes/ghealth.json` here, or by copying an existing authorization
   from another installation. The file holds `client_id`, `client_secret` and
   `refresh_token` — never commit it (the repository `.gitignore` excludes
   `ghealth.json`, keeping only the `.example` template). It must stay uid
   1000, mode 0600; the container mounts it read-only at `/creds/ghealth.json`
   and only `deploy/ghealth-sync` inside that one container ever reads it — it
   never reaches the panel, the broker, or any agent.
2. Set `HEALTH_DB` on the `ghealth` service in `docker-compose.yml` to the
   exact database you want it to update.
3. Start it explicitly: `docker compose --profile ghealth up -d ghealth` (or
   set `COMPOSE_PROFILES=ghealth` before `docker compose up -d`).

Cadence defaults to every 6 hours (`GHEALTH_INTERVAL_SECONDS=21600`; the
minimum is 900 seconds). Each run fetches a 3-day lookback window
(`HERMES_GHEALTH_LOOKBACK_DAYS`, clamped 1–14) so one missed cycle still gets
caught up on the next. A failed run is logged to the container's stdout/stderr
and retried on the next cycle — the container never exits because of one bad
run. Check freshness on the panel's Integrations card (the "Wearable" row
reads the same `daily_metrics` `source='fitbit'` rows this collector writes);
`docker compose logs ghealth` shows one secret-free status line per run
(UTC timestamp, exit code, duration). Roll back by stopping the container
(`docker compose stop ghealth`, or `docker compose --profile ghealth down`) —
it holds no socket and no persistent process elsewhere; stopping it simply
stops future imports.

## Limits (phase 1 of multi-user)

This image ships exactly the Phase 1 multi-user scope described in
[MULTI_USER.md](MULTI_USER.md#phase-1-scope-this-branch): per-user `health.db`
and vault, admin user management, and cross-user isolation. It does **not**
include the Hermes chat/agent integration, Telegram, the Hevy scheduled
collector, or the standalone MCP server — those remain single-process,
single-account integrations documented in [DEPLOYMENT.md](DEPLOYMENT.md) and
are out of scope for this container image. The optional `ghealth` service
above is the one exception: it is still single-account and never started by
default, but it runs as a container rather than requiring the separate
systemd/cron host DEPLOYMENT.md otherwise describes.
