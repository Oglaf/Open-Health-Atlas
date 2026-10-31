# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A self-tracking individual reviewing their own health records (sleep, training, mood, nutrition, symptoms, labs) on their own machine. They want to compare periods, understand patterns, and check the evidence behind them. Some also connect an MCP-compatible AI client of their choice to query records.

## Product Purpose

Open Health Atlas brings health records from many sources into one local application and shared timeline. Users compare periods and inspect the sources, units and calculations behind every result. Success: the user can trust what a number means and where it came from.

## Positioning

Local and evidence-first. Calculations are deterministic and run inside the application; provenance, units and data coverage stay visible; an external model only interprets. Associations are presented as bounded evidence, never as causal conclusions. A cloud AI-coach app could not truthfully claim this.

## Operating Context

Runs locally (Flask panel, Docker, or a macOS desktop wrapper around the same web UI). Data enters via validated imports and entry tools. Optional MCP client connection for interpretation. A read-only fictional browser demo exists.

## Capabilities and Constraints

- Canonical records, deterministic calculations and validated writes stay under application authority.
- Time, unit, provenance and identity contracts must be preserved in anything the UI shows.
- Readiness states (sufficient, never logged, unavailable, not implemented) are real states the UI must represent honestly.
- Existing wired workflows and CLI compatibility must be preserved.

## Brand Commitments

- Name: Open Health Atlas.
- Created by Kajeesan Jeevendra; AGPL-3.0; public attribution and licenses must be preserved.

## Evidence on Hand

- Fictional demo and screenshots (docs/assets, docs/SCREENSHOTS.md). All UI examples must use fictional data; never fabricate real-looking personal health data or testimonials.

## Product Principles

1. Show the source: every figure should be traceable to records, units and calculation.
2. Keep deterministic observation, model inference and general knowledge visibly distinct.
3. Fail honestly: surface missing, partial or insufficient data instead of smoothing it over.
4. Never imply causation from association.
5. Privacy by default: local first, fictional data in anything shared.

## Accessibility & Inclusion

No specific standard confirmed; open decision.
