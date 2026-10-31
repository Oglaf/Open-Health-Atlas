# Product authority

Initially owner-approved on 2026-08-17, with the local MCP extension approved
on 2026-09-16. This document defines the product boundary for
OpenHealthAtlas. Code, tests, demonstrations, and synthetic runtimes do not
override it.

## Local MCP extension

On 17 September 2026 the owner authorized packaging the existing application
as a self-contained macOS desktop app, with guided fictional/personal/imported
workspaces and an optional stable local MCP executable. This changes
installation and local runtime ownership, not the deterministic engine or the
external client's responsibility for interpretation. No built-in provider
chat, model download, external Hermes activation or paid signing service is
authorized by the implementation alone. Release status is tracked separately
in [desktop acceptance](DESKTOP_ACCEPTANCE.md).

The owner approved a standalone, provider-neutral MCP connection for reading
and analysing a user-selected local OpenHealthAtlas database. The external
MCP client owns the conversation and chooses its model. Hermes remains the
existing Telegram and integrated-conversation runtime. The new connection
adds no model runtime, API-key chat, health write operation, or schema change.
Existing fictional Hermes adapters retain their separate configuration and
delivery rules; their historical acceptance does not certify other clients.

## Responsibility table

| Responsibility | Sole product authority | Boundary |
| --- | --- | --- |
| Conversation, question understanding, follow-up questions, model reasoning, hypothesis ranking, and user-facing explanation | The user's external AI client: Hermes or a compatible MCP client | OpenHealthAtlas supplies deterministic evidence and does not generate model prose. |
| Canonical health records and validated reads and writes | OpenHealthAtlas | External clients may request their exposed operations but do not own the record. Standalone MCP access is read-only. |
| Feature definitions, deterministic transformations, readiness decisions, statistics, and findings | OpenHealthAtlas governed engine | The target path is registry -> frame -> readiness -> association -> finding evidence -> ledger. |
| Evidence identity, provenance, ancestry, fingerprints, and validation | OpenHealthAtlas | External clients must preserve returned references and may not invent evidence. |
| Conversation transcripts and continuation identity | The active external conversation surface | A chat transcript is not an evidence-backed interpretation record. Existing Hermes chat adapters retain their own continuation rules. |
| Optional interpretation storage | OpenHealthAtlas `synthesis-v1` | Write-back is off by default. When explicitly enabled later, `synthesis-record` is the only canonical envelope; vault Markdown is a derived presentation, not another authority. |
| Statistics, visualizations, and evidence inspection in the web dashboard | OpenHealthAtlas web dashboard | Browser chat is optional backlog and is not required by the accepted Telegram journey. |

## Synthetic reference retirement

The obsolete in-repository interpretation runtime and its injected test-only
Flask endpoints have been removed after tracing imports, dynamic references,
demo callers, and frontend routes. They had no wired product UI consumer.
This removes no supported input or display control. Top-level engine contracts,
deterministic ledger and `synthesis-prepare`, validated optional `synthesis-v1`
storage, and genuine Hermes integration remain. Historical receipts describe
their original snapshots and are not new verification of the current tree.

## Fork deployment: broker-mediated MCP ingest (this fork)

Owner decision, 2026-10-27: Christian Silva, operator of this fork (Oglaf fork
of kajeesan/Open-Health-Atlas, AGPL-3.0-only) and owner of this fork's
deployment, approved in his own words: "Extend MCP, my goal is to hermes write
calories, exercise routines and etc…". This authorizes a bounded,
broker-mediated `health_write` MCP capability in THIS deployment only:

- restricted to the panel's own validated writer subcommands already declared
  in `deploy/bridge_commands.py` (a fixed exposure subset; nothing new);
- per-user, carrying the same `user` resolution semantics as reads;
- socket-only: every write crosses the private Unix-socket broker's
  SO_PEERCRED gate, flag whitelist, stdin rules and append-only audit (the
  MCP never touches SQLite for writes and never writes files itself);
- refused everywhere the broker is not configured (fail closed).

Trust caveat recorded with the decision: the connected MCP client chooses
`user`, so whoever can talk through the health coach can write data into any
user the deployment lets that socket reach. Nothing else in this document is
widened: no new model or interpretation runtime, no schema change, and the
standalone MCP remains read-only in every mode where no broker socket is
configured.

## Fork deployment: body composition analysis report (this fork)

Owner decision, 2026-10-27: Christian Silva, operator of this fork and owner
of this fork's deployment, approved in his own words: "I want to improve
open-health-atlas to support all information from this report" (a
multi-frequency BIA "body composition analysis report", Relaxmedic-style —
weight/body-fat/muscle/water/protein/mineral breakdown, segmental fat and
muscle balance, bioelectrical impedance per frequency and segment, obesity
and body-type assessment, weight control targets, and other indicators such
as visceral fat grade, BMR, SMI, body age and WHR). This authorizes, in THIS
fork only:

- an additive schema change (Migration 008: `body_composition_scans`,
  `body_composition_components`, `body_composition_segments`,
  `body_composition_impedance`) — no existing table is altered or dropped;
- a validated writer (`log-body-composition`) that goes only through the
  existing validated CLI/broker path (closed-schema JSON on stdin, the same
  transaction/commit discipline as every other toolkit command), exposed to
  the panel and to MCP `health_write` exactly like the writers approved in
  "Fork deployment: broker-mediated MCP ingest" above;
- a read (`body-composition`) and a panel display section, built the same
  way as every other read-only dashboard card.

Nothing else is widened: no model or interpretation runtime is added, no
diagnostic or clinical claim is attached to the stored bands/evaluations
(they are the device's own printed labels, stored verbatim), and the
standalone MCP stays read-only in every mode where no broker is configured —
same boundary as the existing broker-mediated ingest decision.

## Fork deployment: guarded Hevy CSV import on the panel (this fork)

Owner decision, 2026-10-28: Christian Silva, operator of this fork and owner
of this fork's deployment, asked in his own words: "all hevy data is located
in /training right? if it is we could add an import button at the very top."
(/training is the page titled "Body"; Hevy strength data feeds its
progression, radar and ratio cards.) This reverses, for ONE new and narrowly
guarded command only, the earlier posture that bulk imports are
collector/agent-only and the panel is read-only:

- no schema change;
- the panel stays read-only for every OTHER bulk import — the existing
  collector-only `import-hevy`, `import-hevy-json`, `import-hevy-routines`,
  `import-hevy-templates` and `import-hevy-body` commands stay OFF both
  broker allowlists, unchanged (tests/test_bridge.py and
  tests/test_local_mcp_write.py pin this);
- the one new command, `import-hevy-upload`, takes the CSV on stdin only
  (never a filesystem path), caps the upload well under the broker's 8 MiB
  request limit, and validates the export BEFORE deleting any existing
  `source='hevy'` row: the header must carry `start_time`, `exercise_title`,
  `set_index` and one of `weight_kg`/`weight_lbs`; there must be at least one
  data row (an empty export is refused, matching the CLI importer's existing
  "empty export clears history" contract — refusing it here prevents an
  accidental wipe); and every row's `start_time` must parse, or the whole
  import is rejected naming the first unparseable value (a CSV that would
  silently store a NULL date has caused a real past incident for the owner);
- `--dry-run` performs the same validation and returns the same summary
  (rows, distinct workouts, exercises, date range, existing row count) without
  writing; a real run still replaces only `source='hevy'` rows in one
  transaction, preserving hand-logged sets, and rolls back fully on any error;
- the write crosses the existing broker socket and its append-only audit,
  exactly like every other panel-triggered writer — the panel never opens
  SQLite directly and the CSV is never written to disk.

## Fork deployment: scheduled Google Health collector container (this fork)

Owner decision, 2026-10-28: Christian Silva, operator of this fork and owner
of this fork's deployment, instructed, about the Google Health collector:
"Cadence: OK" (every 6 hours), "Weight: I will not always import body scan so
importing from fitbit is ok." and "merge the code and etc. then proceed".
This authorizes, in THIS fork only: a scheduled collector container
(`ghealth`, profile-gated, never started by default) in the Docker medical
stack that runs `deploy/ghealth-sync` in a new container mode
(`HERMES_GHEALTH_MODE=container`) and writes validated data through the
existing `import-google-health` command into ONE configured user's
`health.db`.

- no schema change;
- the collector-only `import-google-health` and `collector-run-record`
  commands stay OFF both panel and broker allowlists — unchanged
  (`deploy/bridge_commands.py`; tests/test_bridge.py pins it, and the
  behavior keeps passing without edits to that allowlist);
- the `ghealth` container is the only new writer this decision introduces,
  and it uses the same trust model as the existing root systemd collector:
  the OAuth credentials live in a file mounted only into that one container
  (`/creds/ghealth.json`, uid 1000, mode 0600, never committed — see
  `deploy/docker/ghealth.json.example`) and that secret never reaches the
  panel, the broker, or any agent; `deploy/ghealth-sync` never opens the
  database itself — every write crosses `health.py`'s validated
  `import-google-health`/`collector-run-record` commands, exactly as the
  systemd path already does;
- Fitbit-sourced weight lands in `body_metrics` with `source='fitbit'`, and
  the existing dashboard/engine source order (manual > fitbit > BIA scan >
  Hevy) already ranks it correctly against every other weight writer; no
  ranking or display change was needed.

## First accepted product journey

The first accepted journey is restricted to the fictional
`green-days-actionable-v1` fixture:

`User -> Telegram -> genuine Hermes on Oracle VPS -> OpenHealthAtlas deterministic tools -> Hermes -> Telegram`

| Journey step | Active entry point | Authority and accepted behaviour |
| --- | --- | --- |
| User question and reply transport | Existing Telegram bot and genuine Hermes gateway | External Hermes owns the conversation and final interpretation. |
| Deterministic tool discovery | `deploy/hermes-openhealthatlas-mcp` | The accepted Green-day sequence uses six bounded, fictional-only tools. The server also defines two separately bounded recovery tools. It exposes no model and no write-back tool. |
| Observations | `feature-frame` | OpenHealthAtlas returns exact fictional observations and provenance. |
| Readiness | `data-readiness` | OpenHealthAtlas decides whether the bounded data is sufficient. |
| Analysis | `analysis-refresh` | OpenHealthAtlas calculates and records the governed Green-vs-non-Green analysis. |
| Feature meaning | `feature-registry` | OpenHealthAtlas supplies registered actionability, timing, and confounder metadata. |
| Selected evidence | `finding-evidence` | OpenHealthAtlas replays exact selected findings and their evidence identities. |
| Synthesis boundary | `synthesis-prepare` | OpenHealthAtlas returns ledger-owned references with `model_invoked=false`; genuine Hermes reasons afterward. |
| Optional storage | Not exposed by the accepted MCP | No interpretation is stored. A future explicit write-back may use only validated `synthesis-v1`. |

The sanitized runtime receipt is
`tools/project_map/evidence/fictional-telegram-acceptance-2026-08-17.json`.
It proves this one bounded journey, not other domains, browser chat, private-data
operation, or every route in the repository.

## Next bounded domain: recovery

The owner selected one recovery question as the next capability. Recovery is
not reduced to resting heart rate. `openhealthatlas_recovery_snapshot` reads a
fixed fictional anchor and returns only eligible deterministic sleep, HRV, and
resting-HR components while keeping training recency, effective sets,
performance trend, soreness, exclusions, and missingness separately visible.
HRV and resting-HR baselines must use the same source as the current
observation. Hermes must ask which returned focus the user wants to inspect
before calling `openhealthatlas_recovery_detail`.

The former 58/100 Recovery evidence is preserved as historical evidence but is
superseded as a current result. It mixed Apple and Fitbit baseline semantics
after selecting a Fitbit current observation. Under the source-consistent
policy, the comprehensive fictional fixture has only ten prior Fitbit rows for
HRV and resting HR, below the required fourteen. Sleep remains available at
92, but one eligible component is insufficient for an overall score or band.

Genuine Hermes completed the corrected `insufficient_data` snapshot, asked for
one bounded focus, returned the user-selected training detail, preserved the
unknown overall state, and showed the exact model-sharing disclosure in both
Telegram replies. The current sanitized acceptance is
`tools/project_map/evidence/fictional-recovery-item6-acceptance-2026-08-17.json`.

The earlier localhost Recovery parity receipt is bound to the superseded
58/100 result. The dashboard remains connected to the governed Recovery API,
and its corrected `insufficient_data` rendering was subsequently verified with
fictional data during the September refactor; see `VERIFICATION.md` for the
exact browser and external-runtime acceptance scopes.
This does not affect the owner-selected primary Telegram transport; browser
chat remains optional backlog.

## Phase 4 consolidation boundary

The Green-day dashboard now reads the governed
`outcome-associations` result for `subjective.day_rating` in
`green-vs-non-green` mode. Against the accepted
`green-days-actionable-v1` fixture and exact 2026-05-17 through 2026-06-30
range, the CLI, API, dashboard, and accepted Hermes tool evidence share one
input fingerprint and the same three finding evidence fingerprints. The
dashboard displays deterministic statistics, readiness nuance, and evidence
identity; it does not generate Hermes interpretation. The localhost developer
server keeps the comprehensive fictional Recovery database on its primary
broker and serves this Green-day card from a second manifest-verified broker
that permits `outcome-associations` and its bounded background start/status
commands for the same fixed outcome/range. Job state lives in a private derived
sidecar. Canonical health write attempts against that broker are rejected, and
its fixture database hash remains unchanged after analysis.

Insight Explorer may attach at most three identifier-only selected findings to
an optional scoped browser conversation. The server owns the conversation
range. A selected finding becomes verified chat evidence only after genuine
Hermes replays the exact finding through OpenHealthAtlas in the same turn.
Verified receipts are stored and rendered separately from Hermes prose.
Ordinary browser turns remain backward-compatible and do not acquire an
evidence claim merely because no evidence was selected.

`synthesis-v1` remains the sole optional interpretation envelope. Its schema
accepts bounded, canonical external provider and model identifiers while
preserving legacy rows and all evidence-reference validation. The installed
fictional adapter config sets `synthesis_writeback_enabled` to `false`, its
skill forbids the call, panel trace validation rejects it, and the accepted MCP
does not register it. Write-back therefore remains off by default and requires
a separate explicit owner-controlled config decision. This structural
capability is not proof that a genuine Hermes interpretation was invoked or
stored.

The sanitized consolidation receipt is
`tools/project_map/evidence/competing-paths-consolidation-2026-08-17.json`.
Its accepted Green-day parity is deliberately fixture-bounded. The broader
`comprehensive-persona-v1` Green-day calculation currently exceeds the broker
timeout and is not represented as accepted runtime proof. Genuine browser chat
also remains a separate proof-level gate.

## Phase 5 acceptance proof levels

Completion is now recorded per named journey, fixture, source snapshot and
transport. Proof does not transfer between levels: deterministic replay does
not prove Hermes reasoning; the simulated panel harness does not prove a
genuine model; a genuine direct or Telegram turn does not prove browser chat;
and evidence from another fixture or source snapshot cannot be inherited.

For the primary fictional Recovery journey, deterministic execution, synthetic
wiring, the genuine two-turn Telegram response, default-off storage, and the
fictional provenance boundary are recorded independently. The current Telegram
turn has a same-turn phase-2 tool receipt and an operational model/provider
attestation. The VPS is a source snapshot rather than a git checkout, and the
provider/model identity is not cryptographically attested per turn; both limits
remain visible in the receipt. Genuine direct transport, genuine browser chat,
live Telegram fault injection, and enabled interpretation write-back remain
unverified. The later Item 6 acceptance now proves the Recovery-specific
insufficient-data behaviour without rewriting this earlier proof-level ledger.
Browser chat is optional backlog for the owner-selected primary Telegram
journey; that makes it non-required, not passed.

The Item 5 VPS cutover changed only the isolated fictional adapter, its
root-owned configuration, and the Hermes skill. The accepted eight-tool MCP
surface was retained, `synthesis-record` remained absent, and a negative
write-back probe was rejected without changing the fictional database or audit.
No browser, panel, private-data, or deterministic-engine deployment was part of
this cutover.

The base proof-level ledger is
`tools/project_map/evidence/fictional-primary-telegram-proof-levels-2026-08-17.json`.
Read it together with the Item 6 Recovery supersession overlay below. Its
`Working` status means the proof taxonomy is established and completion claims
are scoped honestly. It does not mean every gate or transport passed.

## Phase 6 fictional Recovery evidence boundary

The accepted fictional Recovery projection now contains a versioned
deterministic policy, selected source-row locators, transformations,
whole-result evidence identity, component exclusions, incomplete ancestry, and
the exact fields sent to genuine Hermes. Both accepted Telegram replies end
with the deterministic `openhealthatlas-recovery-disclosure-v1` block. That
visible block names the shared evidence container, field groups and paths,
whole-result identities, exclusions and ancestry gaps; it does not enumerate
every locator, transformation or policy value. The model-facing projection
excludes private data, database and vault contents, raw history rows, and the
verbatim soreness note. Interpretation write-back remains disabled.

This closes the bounded fictional Recovery gates for visible missingness and
external-sharing disclosure, and it advances qualitative-field minimization
and threshold governance. It does not close Item 6 system-wide. Raw
provider/sync ancestry and training mapping ancestry are incomplete; the
Telegram disclosure does not visibly name the readiness policy ID/version or
the exact qualitative-source consent/version record; final prose does not cite
every fact individually; and the Telegram gateway does not append an immutable
non-model safety block if Hermes ignores its reply contract. Real/private-data
consent, retention, provider transfer, deletion, broader vault and collector
ancestry, and unrelated visible threshold policies remain unapproved.

The canonical Item 6 ledger is
`tools/project_map/evidence/fictional-recovery-item6-acceptance-2026-08-17.json`.
Its accepted Recovery slice is `Working`; Item 6 remains `Partially built`.

## Generic Hermes interpretation authority

The server-rendered deterministic block is the authoritative numeric and
evidence record. The following Hermes interpretation is explicitly labelled
model-generated and may naturally reference, round, compare, and combine
numbers and dates from the supplied evidence. It is not required to reproduce
server-generated claim lines verbatim. `allowed_scalar_claims` is retained only
as backward-compatible contract metadata and is not an interpretation
allowlist.

Owner decision, 2026-08-19: do not add a new numeric, date, wording, exact-line,
or equivalent generic-prose restriction without explicit owner approval. The
existing fictional-only, private-field, write-back, untrusted-identity,
non-diagnostic, and non-causal boundaries are unchanged by this decision.

## Retained compatibility routes

The local synthetic runtime and its interpretation endpoints are retired as
described above. Superseded compatibility routes retain their separate
fail-closed rule.
`/api/chat/send`, `/api/chat/history`, `/api/insights/brief`, and
`/api/insights/correlations` return HTTP 410 in every normal application
process. Their retained implementations require Flask test mode plus the
explicit `ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES` override. The scoped
conversation API, governed outcome analysis, and governed synthesis history
are their supported boundaries. Legacy `/api/insights/signature` now follows
the same fail-closed rule and returns HTTP 410 in normal application processes;
`day-signature` is absent from both panel broker allowlists. Its retained
implementation is a test-only compatibility fixture.

The historical pre-retirement path classification and gates are recorded in
`tools/project_map/evidence/product-authority-route-inventory-2026-08-17.json`.

The following are not evidence of a working product journey:

- Code presence or Graphify relationships.
- Unit, integration, or synthetic end-to-end tests.
- Historical output from the retired local synthetic interpretation gateway.
- An interpretation object stored only in an in-memory fixture store.
- A deterministic calculation alone as proof of an external model's interpretation.

## Change rule

Do not add another model/provider runtime, interpretation stack, or prose
generator inside OpenHealthAtlas. Changes to this authority require an explicit
owner decision and a corresponding update to this document before product code
is expanded.

## 2026-10-28 — Fork deployment: unused-feature removal (owner: Christian Silva)

Owner instruction, verbatim: "before proceeding, there are some features that's usless to me, so we can remove it, less code to rewrite, remove these features: Models and Spend -> remove Active Model and OpenRouter spend, and change fitbit sync. Training -> I dont do tests, remove Athletic profile, same for strenght ratio and Quarter progress. Also remove Goals. Mind -> Completely remove this feature. External care -> remove feature. Labs -> Remove feature. Insight Explorer -> Remove feature. Data -> Remove Weather °C · UV, Today outside, Workouts min by type, Workouts km by type, Workouts kcal by type, Log a body composition scan (we made our own), Air quality European AQI, Medication and vitals — BP · resting HR · dose mmHg · bpm · mg, Mood".

Decision: Removal is at the PRODUCT SURFACE (nav, pages, panel routes, their JS, tests, docs, and the bridge/agent-command allowlist entries that only they used). No database table is dropped and no schema version/migration changes; dormant engine code (health.py/CLI subcommands and importers that no UI calls) stays and is listed for a possible later cleanup. Kept surfaces keep working: Dashboard (with its Green-day analysis hero — the governed job endpoints moved to app/routes/green_days.py under the same /api/insights path), Consistency, Body (body map activation/pain + physio loop, Muscle Balance, V-taper, Weight, Progression, Weight-to-strength, Hevy CSV import), Nutrition + recipes, Recovery (+ drills), Data Explorer (all remaining cards incl. the BIA display + day rating + sleep/water/training volume/recent activity), Integrations & privacy (rebuilt, per-user), the new Wearable sync page (formerly Models & spend — model/spend cards removed), Export, Vault notes, Admin.

Also authorized same date, consequence of removal: the Telegram/agent cards in Integrations and Models & spend went; the Fitbit re-auth/token cards are replaced by DB-backed collector status (source_sync_runs + daily_metrics freshness) that works for every account. Pain capture moves off the panel chat (chat removed with Insight Explorer); it will re-land as manual logging/MCP pain-log.

## 2026-10-29 — Fork deployment: per-user Profile & targets (owner: Christian Silva)

Owner instruction, verbatim: "if something depends on hermes, rewrite to allow manual insertion or MCP".

Decision: this reverses the upstream rule "capture is agent-only / panel is analysis-only" for ONE narrow, specific case: per-record validated writes by the logged-in user on their OWN data, for the existing T43/T44/§4b owner-config setters that were previously agent/SSH-only. Newly allowlisted for the panel bridge (deploy/bridge_commands.py COMMAND_FLAGS) and the local MCP's health_write (scripts/openhealthatlas_mcp.py HEALTH_WRITE_COMMANDS): `profile-set` (height_cm/sex/dob/activity_fallback), `phase-set` (diet phase), `nutrition-target-set` (kcal/protein_g override), `planned-time-set` (wake/bed/workout/dose planned time + tolerance). Positional values and all semantics stay validated by health.py exactly as before; this only lifts the allowlist exclusion. Every write is still per-user (the panel session or the MCP's explicit `user` argument, never a free-form path) and fully audited by the broker, same as every other panel write — no schema change, no migration.

A new self-service broker command, `user-profile-merge` (SELF_SERVICE_COMMANDS, distinct from ADMIN_COMMANDS), lets a non-owner user merge a partial JSON patch into their OWN profile.json (the existing per-user HERMES_* nutrition overlay: nutrient targets, phase offsets, protein-per-kg) — a key mapped to JSON null removes it. Unlike the admin-only `user-profile-write`/`user-profile-clear` (which take an explicit target id and replace the whole file), `user-profile-merge`'s target is always the CALLING identity from the request's own `user` field; it is refused for the bootstrap owner (who has no profile.json — it reads installation-wide configuration unchanged) and for any user id that doesn't resolve under BRIDGE_USERS_ROOT. Value-level validation (nutrient target names against the known catalog, finite positive numbers, exact cut/maintain/bulk phase keys, sane numeric bounds) lives in deploy/bridge_commands.py next to the existing key/type whitelist (`validate_profile`), shared by the panel's fast-feedback checks and the broker's authoritative ones, so a malformed value is refused before it can take a user's own health.py child process down at import.

Also authorized same date: a new self-service "Profile & targets" page (`/profile`, panel nav + mobile "More" sheet) composes these five commands plus read-only db_read views of the user's own owner_profile/nutrition_targets/planned_times rows into one editor — replacing the Nutrition page's old CLI-only setup instructions for everything except the owner account's three installation-wide nutrition JSON settings, which still require the command-line toolkit or an admin-provisioned separate account. Bulk imports and every other collector-only writer stay off every allowlist, unchanged.

## 2026-10-29 — Fork deployment: manual daily capture (owner: Christian Silva)

Owner instruction, verbatim: "if something depends on hermes, rewrite to allow manual insertion or MCP". Scoped down the same day to: "manual capture (scope shrunk): day rating / evening check-in (drives Consistency, dashboard rings and heatmap, all empty without it; no panel form exists), water, supplements, pain log. Labs/quarterly tests/Mind/care are out of scope now."

Decision: the same narrow reversal as the 2026-10-29 Profile & targets entry above — per-record validated writes by the logged-in user on their OWN data — extended to the daily-loop capture commands that previously had no panel path at all. Newly allowlisted for the panel bridge (deploy/bridge_commands.py COMMAND_FLAGS) and the local MCP's health_write (scripts/openhealthatlas_mcp.py HEALTH_WRITE_COMMANDS): `water-add` (hydration, additive — NOT the generic `log intake` writer, which replaces the day's total), `supplement-log` (taken/dose/time for an existing active supplement_products row), `pain-log` (region/side/intensity/quality/pattern/red-flags/note — reversing the §3g "collector/agent-only" rule for this ONE writer; self-test-log, exercise-trial-log and physio-void stay off every allowlist, unchanged). Positional values and all semantics stay validated by health.py exactly as before; this only lifts the allowlist exclusion. Every write is still per-user and fully audited by the broker, same as every other panel write — no schema change, no migration. Bulk imports stay off every allowlist, unchanged.

Also authorized same date: a new "Daily log" page (`/log`, panel nav + mobile "More" sheet) composes day rating (`day-rating`, back-dateable up to 14 days), the nightly "kept your word" evening check-in (`log-commitment` + `commitment-set`/`commitment-list` for commitment selection/creation) and timed energy/focus/mood spot readings (`checkin`), quick-add water (`water-add`), active-supplement taken/dose/time capture (`supplement-log`), and pain reports (`pain-log`) into one capture surface — the previously-empty gap behind the Consistency rings, dashboard heatmap and habit streaks. Supplement products and the habits_log streak ledger remain collector/MCP-only (no panel editor for either): a product must already exist in `supplement_products` to be logged against, and `habits_log`'s streak/xp bookkeeping has no validated single-record setter to allowlist.

## 2026-10-31 — Fork deployment: Nutrition goals and meals (owner: Christian Silva)

Decision: the Nutrition page no longer logs consumption (no portion eat/prep steppers, batch form, intake tiles or trends, "logged today" feed). It is a goals page and a meals page. Goals are editable in place through the existing Profile & targets writers (`nutrition-target-set` for kcal/protein overrides, `phase-set`, and the self-service `user-profile-merge` micronutrient targets for non-owner accounts). Meals are created, edited and deleted from the panel: two new validated per-record writers, `meal-save` (one JSON document on stdin: name, meal type, grams per portion, portions in the batch, per-portion nutrients, optional ingredient lines) and `meal-delete` (one exact recipe id), are allowlisted for the panel bridge only (deploy/bridge_commands.py COMMAND_FLAGS, no flags) — not for the local MCP's health_write. Per-portion values are stored as the batch total in `recipe_nutrients`, the shape the importer already writes, so freezer, eat and detail math is unchanged; an update rewrites only the nutrients supplied and rescales the rest when the batch weight changes; a delete removes the meal, its nutrients, freezer portions, restock state and ingredient sidecar and leaves past `nutrition_log` rows. `/eat`, `/prep`, `/log-food` and every read route stay for agents and the dashboard. The Cronometer recipe import (`import-recipes`) and the agent-only `recipe-tag` and `recipe-ingredients-set` stay off the panel allowlist, unchanged. Every write is per-user and audited by the broker — no schema change, no migration.
