# Validation results

## T032 — local corpus and User Story 1 checkpoint (2026-10-03)

Scope: T032 only. No application behavior, dependencies, checklist markers or
later task status changed. The root README now documents environment/model and
provider registration, supported extraction, Compose build/start/migration,
read-only manifest mounts, test seeding and explicit activation versus owning-office
live approval, refresh/rebuild, corpus/index inspection, retrieval and test commands.

### Database population exercised

Used isolated Compose project `pnw-t032`, a private PostgreSQL database and retained
named volume. Runtime variables were supplied from an untracked temporary local
environment file through both `--env-file` and `APP_ENV_FILE`; no application `.env`
or existing application database was modified. Built the API/web test targets and
migration image from the pinned Dockerfiles. Applied Alembic to the fresh database;
`alembic current` reported `0002_eligibility (head)`.

Mounted `backend/tests/fixtures/sources` read-only at `/corpus` and invoked:

```text
python -m app.ingestion --manifest /corpus/cli-manifest.json --environment test --seed-fixtures --deterministic
prepared=3 chunks=77 review_required=0 failed=0 seeded=3 referrals_seeded=0
```

Executed the README's Python governance snippet to explicitly activate all three
fixture revisions with synthetic local-only reviewers/reasons. These identities
are not university authorization. PostgreSQL inspection established:

| Check | Observed result |
|---|---|
| PostgreSQL / pgvector | 17.11 / 0.8.6 |
| Revisions | 3 active |
| Chunk identity | `test-embedding`, `fixture-v1`, dimension 3 |
| Stored/searchable chunks | 77 / 77 |
| Review events after activation | 9 |
| Full-text index | `ix_chunk_search_vector`, GIN |
| Governance/scope/vector identity indexes | B-tree indexes present |
| Vector ranking | Exact cosine, no approximate vector index |

Ran unchanged import and `--rebuild`, both with `--environment test
--deterministic` and without seeding. Each returned `prepared=3 chunks=77
review_required=0 failed=0 seeded=0 referrals_seeded=0`. Both retained 77 chunk IDs
and nine review events. The ordered chunk-ID fingerprint remained
`abd6f5ea4eba1288bb09d670ab652bcc` before and after both operations.

Executed the README's actual `retrieve` snippet with explicitly registered
`DeterministicProvider`. Registrar, Public Safety and Spring Semester queries all
returned nonempty, conflict-free results; leading results referenced the matching
official Registrar/Public Safety URLs or academic-calendar PDF, with `main-content`
or `page=N` anchors. The constant-vector provider also returns irrelevant semantic
candidates; this exercise proves plumbing and metadata, not real semantic quality.
Grounded-answer tests below verify irrelevant citation rejection.

Built the normal API/web runtime targets, started them with Compose `up -d --wait`
against the same isolated DB, and confirmed healthy services. The website returned
HTTP 200 and its proxied `/api/health` returned `{"status":"ok"}`. Stopped the
validation services afterward, retaining volumes and leaving the existing
`pnw-chatbot-db-1` application container untouched.

### Automated checks

| Check | Result |
|---|---|
| Compose T018 ingestion unit tests | 24 passed |
| Compose T018 PostgreSQL ingestion/refresh tests | 37 passed |
| Compose T019 chat API contracts | 34 passed |
| Compose T019 PostgreSQL ingestion-to-answer tests | 33 passed |
| Combined Compose T018–T019 checkpoint | 128 passed, no skips |
| Compose T020 chat/session component tests (including T031 additions) | 17 passed |
| Compose T020 Chromium student-answer tests | 5 passed |
| Full local backend regression with dedicated PostgreSQL | 564 passed, no skips |
| Full local frontend unit suite | 43 passed |
| Local T020 Chromium tests | 5 passed |
| Local axe smoke check | 1 passed |
| Frontend typecheck, ESLint, production build | Passed |
| Diff whitespace check | Passed |

The full local backend run used a separate isolated container
`pnw-t032-test-db`, loopback port 55432 and dedicated `pnw_t032` database with
pgvector. Command: `T006_TEST_DATABASE_URL=<dedicated-url>
backend/.venv/bin/pytest backend/tests`. The Compose checkpoint used the documented
four selected Python test files with `T006_TEST_DATABASE_URL` pointing to its
private test DB, and the two documented web test commands. Both backend runs
reported two upstream TestClient deprecation warnings; no failures were suppressed.

T019 covers parking, add/drop, integrity, absence, academic standing, graduate
prerequisites and document/table evidence through real ingestion, PostgreSQL
retrieval, verification and HTTP routing. It also checks scope/lifecycle exclusion,
invented or irrelevant citations, expired deadlines, missing context, conflicts,
provider timeout and retirement during generation. AI calls are deterministic
substitutes; the snapshot/scenario policies and approvals are test-only.

### Limits and remaining release work

Browser tests intercept the API with controlled responses. Real PostgreSQL-backed
HTTP grounding is established separately by T019; these results do not establish
browser-to-vendor acceptance. No live vendor transport or university authorization
was supplied. The provider registration and complete-excerpt verification limits
are documented in the README/backend guide. Live-provider review/latency,
manual WCAG review, privacy/security acceptance and student usability remain at
T044–T050. No student launch readiness is claimed.

No extension hooks were registered: `.specify/extensions.yml` was absent before
and after execution. Existing ignore rules covered the detected Python, Node,
Docker and ESLint setup; no ignore changes were necessary.
