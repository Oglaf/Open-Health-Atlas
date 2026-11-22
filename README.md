# Open Health Atlas

[![source: GitHub](https://img.shields.io/badge/source-GitHub-181717?style=flat-square)](https://github.com/Oglaf/Open-Health-Atlas)
[![stack: Python + Flask](https://img.shields.io/badge/stack-Python%20%2B%20Flask-2563EB?style=flat-square)](https://github.com/Oglaf/Open-Health-Atlas/tree/main)
[![license: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-64748B?style=flat-square)](LICENSE)

Local health-records workspace with deterministic calculations, traceable evidence, a Flask dashboard and optional MCP client integration.

## Repository guide

- **Architecture:** The Flask panel, local records, deterministic toolkit and validated write broker have distinct responsibilities. Optional MCP exposes evidence to an external client; local storage does not guarantee that a hosted model processes data locally.
- **Getting started:** This is Oglaf’s fork: clone `https://github.com/Oglaf/Open-Health-Atlas.git` for these changes, then follow [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [docs/TRY_DEMO.md](docs/TRY_DEMO.md). Use supported macOS/Linux with Python 3.11+ and fictional data in isolated directories. Upstream release/demo links below do not claim artifacts built from this fork.
- **Verification:** Follow [docs/TESTING.md](docs/TESTING.md) for focused tests and complete-suite prerequisites. Verify `python scripts/release_manifest.py verify` after documentation/workflow changes. Never use real health records for tests; report optional dependency skips honestly.

**Explore your health records together—and check the evidence behind the patterns.**

Created by **[Kajeesan Jeevendra](https://github.com/kajeesan)** · [AGPL-3.0 licensed](LICENSE)


Open Health Atlas brings sleep, training, mood, nutrition and other health
records into one local application. Compare periods and inspect the sources,
units and calculations behind the results.

Use the dashboard on its own, or connect a compatible MCP client to query your
records and verify evidence with the model you choose. Open Health Atlas runs
the calculations; the external model provides the interpretation.

**[Try the fictional demo](https://kajeesan.com/openhealthatlas-demo/) ·
[Connect an MCP client](docs/LOCAL_MCP.md) ·
[Develop the project](docs/DEVELOPMENT.md)**

**macOS desktop preview:** Open Health Atlas 0.2.9 is a signed-notarized Apple
silicon preview. See the [0.2.9 release page](https://github.com/kajeesan/Open-Health-Atlas/releases/tag/v0.2.9)
and its attached receipts for exact artifact verification. The
[installation and build guide](docs/DESKTOP.md) and
[tested/blocked acceptance matrix](docs/DESKTOP_ACCEPTANCE.md) record the
remaining platform limits.

![Fictional Open Health Atlas dashboard with Day ratings and Habits & streaks first, above the scores.](docs/assets/fictional-dashboard-priorities.png)

*Fictional UI demo snapshot. [Explore the demo](https://kajeesan.com/openhealthatlas-demo/)
· [Image details](docs/SCREENSHOTS.md).*

## Choose your starting point

**New here? [Start here: install and connect your AI](docs/GETTING_STARTED.md).**
The guide includes copy-paste instructions for a setup agent. Hermes, Telegram,
Google Health, Hevy, Cronometer and a Hostinger VPS have their own sections on the separate
[Optional page](docs/OPTIONAL.md).

| I want to… | Start here |
| --- | --- |
| See the interface without installing anything | [Open the browser demo](https://kajeesan.com/openhealthatlas-demo/) — a clickable, read-only fictional snapshot, with no backend or AI connected. |
| Check the macOS desktop preview | [Installation and release status](docs/DESKTOP.md) (Apple silicon, signed and notarized 0.2.9 preview). |
| Run the real panel and try fictional data locally | [Try the local demo](docs/TRY_DEMO.md#run-the-local-panel) — includes the local API and write broker. |
| Use my preferred AI client with local data | [Connect an MCP client](docs/LOCAL_MCP.md) — copyable setup and a fictional query → analysis → evidence example. |
| Change code or contribute | [Develop the project](docs/DEVELOPMENT.md) — environment, relevant tests and contribution guidance. |

The browser snapshot is useful for exploring screens and navigation; its
values are not a fresh evaluation of the current engine. Run the local demo
to execute calculations, save fictional entries and test imports.

## What you can do

- **Bring records into a shared timeline.** Supported imports and validated
  entry tools cover training, wearables, sleep, nutrition, subjective logs,
  symptoms, laboratory records and other health domains.
- **Explore recorded patterns.** Query summaries or periods, inspect training
  exposure and recovery, and calculate bounded associations across selected
  features. Missing data and source differences remain visible.
- **Check how a result was produced.** Findings retain source, time, units,
  sample coverage, transformations and replayable evidence references.
- **Choose your AI client.** The optional local MCP server exposes discovery,
  query, background analysis and evidence tools. It contains no model and
  makes no provider calls itself.

For example, compare recorded sleep with day ratings over a selected period,
then inspect the number of observations, source coverage and evidence behind
the result. An association can suggest a useful question; it does not establish
why the days differed.

## How the pieces fit

| Component | Responsibility |
| --- | --- |
| Open Health Atlas | Local records, deterministic calculations, validated writes, evidence and dashboard. |
| Your MCP client and chosen model | Conversation, tool selection and interpretation of returned evidence. |
| Optional external Hermes | The existing integrated conversations, Telegram and configured automation workflows. |

The standalone MCP connection is read-only and does not require Hermes. Model
credentials belong in the client. A local database does not guarantee that a
client using a hosted model processes returned data locally.

## Current scope

This is a developer-oriented, single-user source release. Current local setup
supports macOS and Linux with Python 3.11 or newer; native Windows is not
supported by the Unix-socket broker and POSIX file locks. The standalone MCP
entry point uses stdio. Clients requiring remote MCP need a separate transport
setup.

A macOS desktop packaging preview includes the existing dashboard, guided
local workspaces and a bundled optional MCP executable. The [0.2.9 preview](https://github.com/kajeesan/Open-Health-Atlas/releases/tag/v0.2.9)
has exact artifact and receipt details in its release notes. Clean-Mac
installation and the minimum supported macOS remain unverified; this is not
a stable-release qualification claim.
Built-in API-key chat, a hosted multi-user service and a bundled always-on
agent are not included. Existing Hermes and external-service
integrations require their own configuration. Results describe recorded data
and bounded associations; clinical validation and causal conclusions are not
claimed. See [known limitations](UNIMPLEMENTED.md) and the
[feature-retention matrix](FEATURE_RETENTION_MATRIX.md).

## Contribute

Start with [Contributing](CONTRIBUTING.md), the [development guide](docs/DEVELOPMENT.md)
and the [code of conduct](CODE_OF_CONDUCT.md). Look for
[good first issues](https://github.com/kajeesan/Open-Health-Atlas/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22)
or propose a focused improvement through the issue forms.

Use fictional or redacted reproductions. Report security vulnerabilities through
[the private reporting channel](https://github.com/kajeesan/Open-Health-Atlas/security/advisories/new),
not a public issue.

## Evidence and technical detail

- [Architecture](docs/ARCHITECTURE.md) and [product boundaries](docs/PRODUCT_AUTHORITY.md)
- [Testing](docs/TESTING.md) and [verified results](VERIFICATION.md)
- [Current implementation checkpoint](docs/REFACTORING.md)
- [Service deployment](docs/DEPLOYMENT.md)
- [Repository checks](https://github.com/kajeesan/Open-Health-Atlas/actions/workflows/checks.yml)

## Creator and license

Open Health Atlas was created by **Kajeesan Jeevendra**. Its product
architecture, data/evidence boundaries and user experience were developed and
assembled through an AI-assisted development process.

Original project code uses the [GNU Affero General Public License, version 3
only](LICENSE) (`AGPL-3.0-only`). Commercial use is allowed subject to its
terms, including applicable source-sharing requirements. See
[licensing and earlier MIT releases](LICENSING.md) for details. Preserve
applicable copyright and license notices.
Third-party components retain their own licenses and credits; see
[NOTICE](NOTICE), [third-party notices](THIRD_PARTY_NOTICES.md) and
[research references](REFERENCES.md). Use [CITATION.cff](CITATION.cff) for citation.

## Technology stack

Python + Flask. Use the tracked manifests and platform-specific setup documentation for exact requirements.

## Project structure

- [`app/`](app/)
- [`toolkit/`](toolkit/)
- [`desktop/`](desktop/)
- [`deploy/`](deploy/)
- [`scripts/`](scripts/)
- [`tests/`](tests/)
- [`docs/`](docs/)

## Development workflow

Tracked workflows: [.github/workflows/checks.yml](.github/workflows/checks.yml), [.github/workflows/desktop.yml](.github/workflows/desktop.yml), [.github/workflows/image.yml](.github/workflows/image.yml). Push/pull-request filters skip ordinary Markdown and `docs/`-only changes; code, dependency and workflow changes still follow the original triggers. Manual, scheduled and tag-based behavior is preserved where configured. Packaged muscle-map and licensing inputs still trigger relevant builds. Documentation plus release-inventory-only maintenance is excluded; regenerate and verify that inventory locally before committing.

## Coding standards

Preserve upstream creator attribution and the license boundaries in [LICENSING.md](LICENSING.md). Follow [AGENTS.md](AGENTS.md), keep data fictional, and maintain the release inventory.

## Testing

Follow [docs/TESTING.md](docs/TESTING.md) for focused tests and complete-suite prerequisites. Verify `python scripts/release_manifest.py verify` after documentation/workflow changes. Never use real health records for tests; report optional dependency skips honestly.
