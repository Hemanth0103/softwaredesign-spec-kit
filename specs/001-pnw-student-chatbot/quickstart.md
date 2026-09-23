# Quickstart Validation Guide

See [data-model.md](./data-model.md) and [contracts/api.md](./contracts/api.md).

## Run

**Current T005 setup:** Start with the [beginner's Docker guide](../../docs/docker.md).
It covers installation, environment setup, local/development/test targets and HTTPS.
Only the starter page and `/api/health` are currently implemented. The workflow
below describes the later full application; migrations require T006/T009 and
corpus loading requires the ingestion tasks.

1. Copy `.env.example` to `.env`; use only test secrets/configuration.
2. `docker compose up --build -d`
3. After T006/T009: `docker compose run --build --rm migrate`
4. Load an approved fixture corpus with active, retired, campus-specific, conflicting sources and referrals.
5. Confirm `docker compose ps` marks DB/API healthy and open the frontend.

## Validate

| Scenario | Expected outcome |
|---|---|
| Supported policy question | Cited `answer` with active official links and applicable context. |
| Campus-dependent question without campus | `needs_context`, then a campus-matching cited answer after follow-up. |
| Past/current deadline | Current term is identified; inactive term is not described as current. |
| Account-specific or unsupported request | `referral`, limitation and active PNW contact; no determination. |
| Conflicting source fixture | `unresolved` plus referral; no synthesized policy. |
| Danger/self-harm/violence fixture | Immediate `emergency` contacts; no normal retrieval. |
| Retire cited source then repeat within an hour | New response excludes it. |
| End session and inspect DB/log fixtures | No message/identity retained. |
| Keyboard + screen-reader + axe scan | WCAG 2.2 AA acceptance review passes. |
| Load test supported fixtures | 95% return within 10 seconds. |

Build test targets with `docker compose -f compose.yaml -f compose.test.yaml build api web`.
Run `docker compose -f compose.yaml -f compose.test.yaml run --rm api pytest` for
backend tests. For each frontend script (`test`, `test:e2e`, `test:a11y`), run
`docker compose -f compose.yaml -f compose.test.yaml run --rm --no-deps web npm run <script>`.
