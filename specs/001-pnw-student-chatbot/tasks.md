---
description: "Implementation tasks for the PNW Student Information Chatbot"
---

# Tasks: PNW Student Information Chatbot

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/api.md`, `quickstart.md`, and `.specify/memory/constitution.md`.

**Organization**: The previous 84 unchecked tasks are consolidated and renumbered T001–T050 in execution order. The three user stories and their priorities are unchanged. Shared governance, context, and safety prerequisites are implemented once in the foundation; stories integrate and validate them.

**Paths**: Implementation paths are relative to the repository root. Feature documents are under `specs/001-pnw-student-chatbot/`.

**Validation**: Tests are required by the specification and constitution. Write the relevant tests before behavior, observe the expected failure, then implement and run them before completing the increment. A test-authoring task may precede its implementation; its passing result is required at the phase checkpoint. Use deterministic AI/OIDC doubles for ordinary tests and the configured real provider for recorded acceptance and normal-service latency checks.

**Scope**: React + TypeScript + Vite, FastAPI + Python, SQLAlchemy + Alembic, and PostgreSQL with pgvector in Docker Compose. Use simple modules, one ingestion command, and three long-running services (`web`, `api`, `db`). Start with exact vector search and no retrieval cache; add cosine HNSW only if measured latency requires it. No extra database, queue, worker service, microservices, or Kubernetes.

## Phase 1: Setup

**Purpose**: Establish reproducible development and testing tools.

- [X] T001 Create the planned `backend/`, `frontend/`, and `infra/postgres/init/` structure and exclude secrets, generated files, and builds in `.gitignore`
- [X] T002 [P] Initialize FastAPI/Pydantic, SQLAlchemy/Alembic, pgvector support, pytest, linting and type checking in `backend/pyproject.toml`; declare a supported Python runtime and pin dependencies reproducibly in `backend/requirements.lock`
- [X] T003 [P] Initialize React/TypeScript/Vite with Vitest, Playwright, axe-core, linting and type checking in `frontend/package.json`, `frontend/package-lock.json`, `frontend/vite.config.ts`, and `frontend/playwright.config.ts`; provide the quickstart test scripts
- [X] T004 [P] Add environment configuration for database, AI provider/models, embedding version/dimension, chunk size/overlap, timeouts, OIDC and CORS in `.env.example` and `backend/app/config.py`; keep credentials outside source control and images
- [X] T005 Add pinned multi-stage API/web images and PostgreSQL/pgvector setup in `backend/Dockerfile`, `frontend/Dockerfile`, `infra/postgres/init/001-extensions.sql`, and `compose.yaml`; include development/test targets, DB/API health checks with `service_healthy`, a private database network/volume, one-off Alembic execution, and frontend proxy `/api` routing with deployment TLS termination; publish no production DB port

**Checkpoint**: Images build and the development/test commands start successfully; database initialization is ready for migrations.

T005 container builds, startup, persistence, development/test targets and local
TLS validation passed on 2026-09-23. See [validation evidence](../../docs/docker-validation.md)
and the [beginner's Docker guide](../../docs/docker.md). T006 and later tasks remain unchanged.

## Phase 2: Foundation and Shared Safety

**Purpose**: Establish the shared database, governance rules and safe decision components before exposing student answers.

- [X] T006 Create SQLAlchemy sessions, UUID/UTC base fields and Alembic configuration in `backend/app/db/session.py`, `backend/app/db/base.py`, and `backend/alembic/env.py`; configure `backend/alembic.ini` and support migrations against fresh or existing database volumes
- [X] T007 Define source/revision models in `backend/app/models/source.py`: source "UUID, unique HTTPS URL, title, owner office, subject", "approval=`draft|approved|rejected`", "lifecycle=`active|superseded|retired`", timestamps; revision "UUID, source FK, immutable SHA-256 content hash, retrieved/effective timestamps", "campus (`hammond|westville|all`)", "optional program/course/term scope, status"; enforce revision transitions "pending_review → approved/active → superseded | retired"
- [X] T008 Define chunks in `backend/app/models/source_chunk.py` with "UUID, revision FK, ordinal, text/heading/table context, citation anchor, `tsvector`, pgvector embedding, embedding model/version/dimension" and the "Immutable retrieval unit." rule; define review events in `backend/app/models/review_event.py` with "UUID, source/revision FK, reviewer OIDC subject", "action (`approve|activate|supersede|retire|resolve_conflict`)", "reason, timestamp" and "Append-only audit. Owner office controls subject source; Dean of Students resolves conflicts."
- [X] T009 Define referrals with "UUID, topic, office, URL and/or phone/email, campus scope, active flag" in `backend/app/models/referral.py`; create the single initial schema migration in `backend/alembic/versions/0001_initial.py`, including vector extension, foreign keys, uniqueness constraints, GIN full-text and B-tree lifecycle/scope indexes; use UTC timestamps throughout and exact pgvector cosine search initially
- [X] T010 Add shared fixtures and test setup in `backend/tests/fixtures/corpus.py`, `backend/tests/fixtures/sources/manifest.json`, `backend/tests/conftest.py`, and `frontend/tests/setup.ts`; include representative official PNW HTML/PDF/document snapshots, source/approval provenance, active and ineligible revisions, both campuses, program/course/term scopes, conflicts, unreadable content, and official referral/emergency contacts; fixture approval applies only in local/test environments
- [X] T011 Add foundation tests in `backend/tests/unit/test_source_rules.py`, `backend/tests/unit/test_decisions.py`, and `backend/tests/contract/test_api_rules.py` for database constraints, owning-office/Dean permissions, lifecycle transitions, eligibility, emergency-first behavior, account-specific referrals, material context, safe errors and no-store responses
- [X] T012 Implement public student API setup, `/api` routing, health endpoint, restricted CORS, rate limiting, trusted HTTPS proxy handling and privacy-safe errors/logging in `backend/app/main.py` and `backend/app/api/middleware.py`; never persist/log question text, student identity, prompts or transcripts, retain only aggregate non-identifying counters, and apply `Cache-Control: no-store` to chat responses
- [X] T013 Implement deployment-configured PNW OIDC token validation and office roles in `backend/app/auth.py`, and source/revision governance in `backend/app/services/source_governance.py`; draft creation never approves content, owner offices approve/retire their sources, Dean of Students resolves escalated conflicts, reasons are non-empty, and each authorized transition appends an audit event
- [X] T014 Implement shared ingestion/retrieval eligibility in `backend/app/services/source_eligibility.py`: "A source is eligible only when approved+active and has an eligible active revision." Enforce dates/scopes and exclude draft, rejected, retired, superseded, unreadable and conflicting evidence; "conflicting active evidence becomes `unresolved` and is excluded until Dean of Students resolution." Recheck DB eligibility before publication and answer return; retirement/supersession takes effect immediately and always within one hour, without a cache or scheduler; if derived retrieval caching is later added, invalidate affected entries immediately on source changes
- [X] T015 Define all five response outcomes and request validation in `backend/app/api/schemas/chat.py` exactly as `specs/001-pnw-student-chatbot/contracts/api.md`: question 1–4,000 characters, optional campus `hammond|westville`, program ≤160, course ≤32, academicTerm ≤80, no unknown fields; require answer/citations/appliedContext, focused question/non-empty requiredFields, limitation/officeName/contactUrl, or emergency guidance/non-empty contacts as appropriate; return privacy-safe 400/429/500 errors
- [X] T016 Implement reusable decision gates in `backend/app/services/decisions.py`: emergency guidance and relevant PNW safety contacts before normal retrieval/generation; no personal record, eligibility, enrollment, degree, financial-aid, disciplinary, housing or registration determinations; focused follow-up only for material missing campus/program/course/term; unsupported/unreadable/ambiguous/conflicting evidence and provider failures produce an explicit limitation with an active appropriate referral, never invented policy
- [X] T017 Add frontend API client and typed outcome handling in `frontend/src/api/client.ts` and `frontend/src/types/api.ts`, including safe service errors and runtime API configuration; run the foundation tests and migration checks before starting user stories

**Checkpoint**: Shared models, approval rules, safety/context decisions and API primitives pass tests. No student answer endpoint is exposed yet. These components are reused by all three stories.

## Phase 3: User Story 1 — Receive a Grounded University Answer (P1)

**Goal**: Prepare the approved PNW corpus and return plain-language answers supported by its actual content and official citations.

**Independent test**: Ingest representative approved sources, then ask parking, add/drop, academic-integrity, absence, academic-standing and graduate-program questions. Verify relevant chunks, official links and applicable term/context, including answers found inside linked documents and tables.

### Tests

- [X] T018 [P] [US1] Add extraction/normalization/chunking/embedding unit tests in `backend/tests/unit/test_ingestion.py` and PostgreSQL pipeline tests in `backend/tests/integration/test_ingestion.py`; cover preserved metadata, unreadable/unsupported formats, all eligible chunks embedded, vector compatibility, populated tsvectors/indexes, repeat imports, failed imports, updated revisions and concurrent status changes
T018 test-authoring validation (2026-09-28): 24 unit cases collect and fail as
expected because `app.ingestion`/`app.ai` are deferred to T021–T026; 19 PostgreSQL
pipeline cases collect but require `T006_TEST_DATABASE_URL` and a running pgvector
database. No test failures are hidden with xfail. Passing ingestion behavior and
real PostgreSQL execution remain required at T032. Existing backend regression:
186 passed, 26 database-dependent cases skipped. Ruff passes for the new tests.
Run from repository root: `backend/.venv/bin/pytest backend/tests/unit/test_ingestion.py
backend/tests/integration/test_ingestion.py`. The test module docstrings define the
proposed internal interfaces for subsequent implementation tasks.

- [X] T019 [P] [US1] Add answer API contract and ingestion-to-answer tests in `backend/tests/contract/test_chat.py` and `backend/tests/integration/test_grounded_answers.py`; verify required fields, validation/errors, approved/context-compatible retrieval, source-backed claims and citations, and rejection of invented URLs, valid-but-irrelevant citations and inactive deadlines

T019 test-authoring validation (2026-09-28): 60 cases collect across
`backend/tests/contract/test_chat.py` and `backend/tests/integration/test_grounded_answers.py`.
The contract run has 12 passing schema checks and 22 expected failures: the student
answer route currently returns 404 and `app.api.routes.chat` is deferred to T030.
The 26 PostgreSQL ingestion-to-answer cases require `T006_TEST_DATABASE_URL`; they
were skipped because no dedicated database is configured. Test docstrings specify
proposed internal injection interfaces for T028–T030; grounding checks use real
ingestion/retrieval/verification with deterministic AI substitutes. No new packages
were required. Ruff passes. Existing backend regressions excluding the deliberate
T018/T019 red tests: 186 passed, 26 database-dependent cases skipped.
Passing behavior and real PostgreSQL execution remain required at T032; no xfail
masks these unfinished implementations. Run from repository root:
`backend/.venv/bin/pytest backend/tests/contract/test_chat.py backend/tests/integration/test_grounded_answers.py`.

- [X] T020 [P] [US1] Add student answer/session tests in `frontend/tests/unit/chat.test.tsx` and `frontend/tests/e2e/student-answer.spec.ts`, covering accessible submission, cited answers, API errors and clearing all conversation state at session end

T020 test-authoring validation (2026-09-29): Added 13 component/session tests
and 5 Chromium browser tests for labeled/keyboard submission, descriptive citations
and applied context, live announcements, safe API errors/recovery, and session
clearing (draft, answers, citations, errors, pending follow-up, all context fields,
storage/remount/reload, and late responses). Tests render the public App and use
controlled API responses with the real client; these do not establish backend
grounding or full WCAG compliance. File comments document the proposed accessible
UI contract for T031. No production code or dependencies changed.
Type checking and ESLint pass. All 26 existing unit tests pass; the 13 new unit
and 5 browser cases fail as expected because the T031 question form is absent.
No failures are skipped or marked expected-failure. Passing behavior remains
required at T032. Run from `frontend/`: `npm test`, `npm run typecheck`,
`npm run lint`, and `npm run test:e2e -- student-answer.spec.ts` (the initial
red browser run used `--timeout=5000`).

### Implementation

- [X] T021 [US1] Implement collection/import and source validation in `backend/app/ingestion/sources.py` using an explicit manifest of governed canonical PNW URLs or local document snapshots; check official HTTPS location, owning-office approval and revision hash before ingestion, preserve provenance, bound fetch size/time, and validate redirects; linked documents require their own approval and changed content is registered for review rather than silently trusted


T021 validation (2026-09-29): Added governed manifest collection, bounded HTTP/local
snapshot reads, official HTTPS/redirect validation, source/revision approval-audit
and hash checks, provenance results, post-fetch governance rechecks, and idempotent
pending-review registration for changed bytes. Collection never approves, activates,
or publishes; changed/previously retired content is not returned for ingestion.
Documented the manifest format, caller transaction contract and limits in
`backend/README.md`. No new dependencies or later-task implementations were added.
The initial 46 focused tests failed on the missing module before implementation;
the completed suite has 59 passing focused tests. Backend regressions excluding
the deliberately red T018/T019 future-implementation suites: 245 passed, 29 database
checks skipped in the initial local run. Docker follow-up (2026-09-29): all 62
focused tests passed, including all three PostgreSQL concurrency/rollback checks;
the implemented backend regression suite passed with 274 tests and no skips
(two upstream TestClient deprecation warnings). Validation used the isolated
`pnw-t021-validation` Compose project and dedicated `pnw_t021_test` database with
pgvector enabled; the deliberately red T018/T019 chat, unit ingestion, integration
ingestion and grounded-answer suites were excluded. Validation containers were
removed afterward, preserving volumes and application data. Ruff and full-app mypy pass.
Run `backend/.venv/bin/pytest backend/tests/unit/test_ingestion_sources.py
backend/tests/integration/test_source_collection.py`; configure the dedicated
PostgreSQL database to execute all three integration checks. T022 onward remains
unchanged; full ingestion/publication validation is still required at T032.

- [X] T022 [US1] Implement HTML and supported PDF/document text extraction plus cleaning/normalization in `backend/app/ingestion/extract.py`; remove navigation/boilerplate while preserving wording, dates, lists, headings/sections, readable tables and citation anchors; reject unsupported or unreliable extraction instead of publishing partial or invented content

T022 validation (2026-09-29): Added deterministic immutable extraction blocks for
UTF-8 HTML, digital PDF and simple DOCX, preserving wording, section/list text,
table rows/cells, HTML fragments, DOCX bookmarks and PDF page locators. Added
hash-locked Beautiful Soup and pypdf dependencies; existing package pins retained.
Unreadable/unsupported content fails the entire extraction, including partial PDFs,
encrypted PDFs, embedded PDF images/forms, unreliable tables and unsupported DOCX
content. Supported-format limits and manual conversion/review are documented in
`backend/README.md`; PDF visual heading levels are not inferred. No publication,
chunking, embeddings or later-task behavior was added.
The existing 10 extraction cases failed on the missing module before implementation;
all 10 now pass, along with 31 focused extraction tests. Backend regression excluding
the deliberately unfinished T018/T019 ingestion/chat/grounded-answer suites:
276 passed, 29 PostgreSQL-dependent checks skipped because no dedicated database
was configured, and two upstream TestClient deprecation warnings. Ruff lint/format,
full-app mypy, dependency compatibility and diff whitespace checks pass.
Run from repository root: `backend/.venv/bin/pytest backend/tests/unit/test_extraction.py`
and `backend/.venv/bin/pytest backend/tests/unit/test_ingestion.py -k 'html or readable or unreadable'`.
T023 onward remains unchanged; full pipeline validation remains required at T032.

- [X] T023 [US1] Implement deterministic, section-aware chunks with configurable size/overlap in `backend/app/ingestion/chunk.py`; retain paragraph/table meaning and ordinals, and preserve source URL/title/owning office, headings/anchors, campus, program/course scope, academic term/effective dates and immutable revision identity through chunk fields and source/revision relationships

T023 validation (2026-09-30): Added pure deterministic word-budget chunking,
immutable chunk fields and optional revision identity, section/anchor isolation,
contiguous ordinals, bounded within-paragraph overlap, and complete table-row
splits with repeated caption/header. Oversized rows and uncertain oversized PDF
tables fail for review rather than truncation. Source URL/title/office and scope/
effective-date/hash metadata remain on the existing revision/source relationships;
persistence wiring remains T025. Documented the interface and limits in
`backend/README.md`. The 6 existing chunk cases and 8 new boundary cases failed
on the missing module before implementation. Focused extraction/chunk validation:
24 passed, 8 embedding cases deselected. Implemented backend regressions: 316
passed, 29 database-dependent checks skipped, 9 deselected; future chat, pipeline
and grounded-answer suites excluded. Ruff and full-app mypy pass. No new
dependencies; T024 onward remains unchanged. Full pipeline validation remains T032.


T023 calendar correction validation (2026-09-30): Chunking now repeats only
explicitly extracted academic-year cells and semester labels across adjacent PDF
pages, separating new year schemas and semesters into distinct chunks. Original
row text, empty column positions and source-page citations are preserved; table
context records header/semester source pages. Pages 2–4 retain preceding headers,
and page 5 Spring/Summer rows explicitly retain the page 4 2035-2036 header.
Missing context, page gaps, intervening prose, unknown sections, inconsistent row
widths and values in unnamed columns fail the whole chunk operation for manual
review. T022 extraction was unchanged.
Eight new regression cases failed before the fix and pass afterward, including
exact ordered row/header/semester/page equality across the entire five-page PDF
with a 100-word budget. Focused T023/existing ingestion checks: 32 passed,
8 embedding cases deselected. Implemented backend regression: 324 passed,
29 PostgreSQL-dependent checks skipped (no dedicated database configured),
9 deselected, two upstream deprecation warnings. Future-task chat, integration
ingestion and grounded-answer suites were excluded; embedding tests were
excluded with `-k 'not embed'`. Ruff lint/format, full-app mypy and whitespace
checks passed. No embeddings, vector storage, T024 or later work was implemented.

- [X] T024 [US1] Implement a small configurable AI adapter in `backend/app/ai.py` with `embed` and `generate_grounded_answer`, deterministic test substitutes and bounded timeouts; embed every eligible chunk with the configured model, record model/version/dimension, and reject missing, non-finite or dimension-incompatible vectors; generation receives only approved retrieved excerpts
T024 validation (2026-09-30): Added the provider-neutral settings-selected AI
adapter, explicit deployment provider registry, immutable embedding identity and
chunk metadata, full-batch vector validation, internal approved-excerpt generation
boundary, structured untrusted drafts, safe errors, bounded waits and concurrent
calls, and explicitly injected deterministic substitutes. No vendor/model is
assumed: deployments must supply a Provider transport; no live-provider acceptance
is claimed. Eligibility and publication remain caller/T025 responsibilities;
claim/citation verification remains T029. Documented transport and timeout
contracts in backend/README.md. Eight existing embedding tests and eleven initial
adapter tests were observed red before implementation. Final focused suite:
41 passed. Implemented backend regression: 350 passed, 29 PostgreSQL-dependent
checks skipped without a dedicated DB, two upstream deprecation warnings.
Deliberately unfinished T019 chat/grounded-answer and T018 DB ingestion suites
were excluded. Ruff across app/tests, full-app mypy and git diff whitespace checks
pass. No new dependencies/settings or later-task implementations. Full pipeline,
DB and live-provider validation remain required at later checkpoints.
Run from backend/: .venv/bin/pytest tests/unit/test_ai.py tests/unit/test_ingestion.py.

- [X] T025 [US1] Store source records, reviewed revisions, chunks, full-text data, metadata and embeddings transactionally in `backend/app/ingestion/store.py`; make unchanged imports idempotent by source/hash/model identity, retain model/version/dimension compatibility, and publish only fully prepared eligible content after rechecking governance; failures must not expose partial chunks/vectors
T025 validation (2026-09-30): Added transaction-owned revision storage with
review/hash/official-URL checks, immutable chunk metadata, populated English
full-text vectors and validated model/version/dimension embeddings. Preparation
occurs outside governance locks; final source-before-revision locks recheck
approval audits, readability, conflicts, dates and lifecycle. Complete writes
commit together; failures roll back without partial retrieval units. Unchanged
imports reuse chunk IDs without reembedding; concurrent imports serialize final
publication, and incompatible dimensions/layouts require a new model/version.
Activation remains the governance service; refresh remains T026. Updated the
integration fixture to record source approval through governance and documented
the storage interface in backend/README.md. Observed the missing-module failure
before implementation. All 25 T025 PostgreSQL cases pass, including concurrency,
write rollback and mid-embedding governance changes. Implemented backend
regression: 404 passed, 1 deselected, no skips (two upstream deprecation warnings).
Excluded future chat/grounded-answer suites and the T026 changed-content case.
Ruff lint/format, full-app mypy and diff whitespace checks pass. Validation used
an isolated pgvector PostgreSQL container/database; no new dependencies.
Run the README storage command with T006_TEST_DATABASE_URL configured.

- [X] T026 [US1] Implement refresh/rebuild in `backend/app/ingestion/refresh.py`: changed content creates a new immutable-hash revision pending owning-office review, approval permits regenerated chunks/embeddings, and activation atomically supersedes the old revision while preserving audit/history; model changes rebuild compatible immutable chunks, and retries never reactivate rejected, retired, superseded or conflicting material
T026 validation (2026-10-03): Added review-first refresh registration with immutable
content hashes, inherited reviewed scope/effective dates, official/source-approval
audit checks, source-before-revision locking, conflict/terminal-source gates and
concurrent idempotent registration. Extraction establishes readability for new
and collector-created pending revisions without approval. Matching existing hashes
preserve identity, provenance and lifecycle; terminal evidence is never reactivated.
Rebuild delegates to T025 atomic storage, preserving old model identities and
rechecking governance after provider work. Activation reuses T013 owning-office
governance, atomically superseding old revisions with append-only audits/history.
Documented the explicit registration → review → preparation → activation workflow;
CLI remains T027. Observed 10 missing-module failures before implementation and
the collector-readability failure before adding that handoff. All 37 PostgreSQL
ingestion cases pass, including changed-content review/history, immutable model
rebuilds, concurrent registration, rollback and activation-audit failure rollback.
Implemented backend regression: 416 passed, no skips (two upstream deprecation
warnings); future chat/grounded-answer suites excluded. Ruff lint, formatting of
changed Python files, full-app mypy and diff whitespace checks pass. The full-format
check reports pre-existing formatting in extract.py and test_extraction.py; these
files were left unchanged. Validation used an isolated pgvector database;
no dependencies or later tasks changed. Run the README ingestion command with
T006_TEST_DATABASE_URL configured.

- [X] T027 [US1] Assemble one synchronous CLI in `backend/app/ingestion/__main__.py` supporting `python -m app.ingestion --manifest <path>` and `--rebuild`, with actionable counts/errors and nonzero failure exits; reuse the API image, support safe reruns and explicit local/test-only fixture/referral seeding, and never let a normal import grant approval

T027 validation (2026-10-03): Added the synchronous module CLI for governed
manifest import and immutable embedding rebuild, explicit deployment provider
factory registration, actionable aggregate counts and privacy-safe nonzero errors.
Normal imports never approve or activate. Changed content commits pending review
and reports its revision ID; per-revision preparation remains atomic and retryable.
Explicit local/test-only snapshot/referral seeding records synthetic approval
audits only for absent identities, preserves existing/terminal governance state,
validates snapshot hashes and official contacts, and rolls back all seeding on
invalid input. The offline provider requires an explicit local/test opt-in.
Documented manifest/provider setup, API-image invocation, fixture/referral inputs,
exit codes, transaction boundaries and validation commands in backend/README.md.
Observed seven missing-module test failures before implementation. All 14 focused
CLI checks (eight unit, six PostgreSQL) pass within the implemented backend suite:
430 passed, no skips, two upstream TestClient deprecation warnings. Future chat
contract/grounded-answer suites were excluded because they require T028–T030.
Ruff lint, changed-file formatting, full-app mypy, module help invocation and diff
whitespace checks pass. Validation used an isolated pgvector container/database;
no dependencies or later tasks changed. A live provider factory must be supplied
by the deployment; live-provider and full-story acceptance remain later checks.

- [X] T028 [US1] Implement hybrid full-text and compatible exact pgvector retrieval in `backend/app/services/retrieval.py`; apply approved/active/effective/campus/program/course/term predicates before ranking and recheck results, preserving conflict information for an unresolved outcome rather than silently choosing another policy; return relevant chunk content and official source metadata, not navigation links alone
T028 validation (2026-10-03): Added exact pgvector cosine and PostgreSQL
full-text retrieval with reciprocal rank fusion, governed scope/date filtering
before ranking, materialized embedding-identity filtering before cosine evaluation,
and fresh eligibility checks after ranking. Returned chunks preserve source IDs,
canonical official URLs/titles, headings, table context, anchors and revision scope.
Relevant conflicts return diagnostics and no answer excerpts, including when the
result limit would otherwise hide them. Semantic candidates require cosine similarity
≥0.5 by default; lexical matches also qualify. Relevance remains candidate selection,
not proof of claim support; T029 must verify and recheck after generation.
Observed four missing-module unit failures before implementation. All 23 focused
checks pass with real PostgreSQL/pgvector, including scope/effective boundaries,
embedding identity/dimension/zero-vector handling, unrelated versus relevant
conflicts and retirement between ranking and recheck. Implemented backend regression:
453 passed, no skips, two upstream TestClient deprecation warnings. Future T029–T030
chat suites were excluded. Ruff, full-app mypy and diff whitespace checks pass.
Reproduce with T006_TEST_DATABASE_URL pointing to a dedicated pgvector database:
`backend/.venv/bin/pytest backend/tests/unit/test_retrieval.py
backend/tests/integration/test_retrieval.py`. No later tasks or dependencies changed.

- [X] T029 [US1] Implement structured generated-answer validation in `backend/app/services/citation_verifier.py`; every citation must match a retrieved eligible official canonical HTTPS URL/title and supporting excerpt, each policy claim must be supported, and applicable context must match; recheck source eligibility before sending the answer and fail safely on unsupported or malformed output

T029 validation (2026-10-03): Added fail-closed internal/public draft validation,
canonical citation construction, exact applied-context checks, full cited-excerpt
support coverage and a fresh database eligibility/content/metadata recheck.
Support accepts complete excerpts with whitespace normalization only; paraphrases
and shortened claims are rejected rather than assuming semantic entailment.
Documented this limit and the T030 caller contract in `backend/README.md`.
Observed missing-module failure before implementation. All 18 unit tests pass;
all 14 real PostgreSQL/pgvector checks pass, including post-retrieval retirement,
supersession, approval, readability, expiry/future dates, conflicts, all four
scope fields and changed title/URL. Implemented backend regression: 484 passed,
no skips, two upstream TestClient deprecation warnings, before the final additional
multi-source/date test; the expanded 18-test unit suite also passes. Future
T030 HTTP/chat suites were excluded. Ruff, full-app mypy and whitespace checks pass.
Validation used an isolated `pnw-t029-validation` container and `pnw_t029_test`
database; no application data was used. Reproduce with `T006_TEST_DATABASE_URL`
and `backend/.venv/bin/pytest backend/tests/unit/test_citation_verifier.py
backend/tests/integration/test_citation_verifier.py`. No new dependencies or later
task implementations were added.

- [X] T030 [US1] Connect safety → account/context checks → eligible retrieval → grounded generation → citation verification → response in `backend/app/services/chat_service.py` and expose `POST /api/v1/chat/answers` in `backend/app/api/routes/chat.py`; use the foundation decisions for all five outcomes, including provider timeout/unavailability, and never run normal retrieval/generation first for emergencies

T030 validation (2026-10-03): Added request-scoped orchestration and the public
validated/no-store endpoint, application-owned engine startup/disposal, emergency
checks before database/AI access, governed account referrals, conservative lexical
scope follow-ups before embedding, eligible hybrid retrieval, bounded embedding/
generation, and final citation verification with a fresh eligibility clock/read.
Conflicts and rejected drafts return unresolved; empty evidence and provider
failures/timeouts return referrals. No question/context/draft storage or logging.
Provider transport registration is explicit through `app.state.ai_providers`,
documented in `backend/README.md`; no fake or vendor is selected automatically.
The conservative scope probe can over-request context on broad lexical matches;
the T029 complete-excerpt verification limit remains unchanged.
Observed 22 existing HTTP failures and 10 new orchestration failures before
implementation. Aligned T019 PostgreSQL fixtures with required source approval
audits, a fixed ingestion clock, established excerpt URL fields, and complete
document/table text; added scope/conflict/provider integration cases and replaced
the obsolete no-chat health assertion with an offline emergency check.
Full backend regression against the isolated `pnw-t030-test-db` pgvector database:
564 passed, no skips, two upstream TestClient deprecation warnings. Ruff, full-app
mypy and whitespace checks pass. No new dependencies. Run
`T006_TEST_DATABASE_URL=<dedicated-pgvector-url> backend/.venv/bin/pytest backend/tests`.
T031 and later tasks remain unchanged; live-provider acceptance, latency and
frontend validation remain at their planned checkpoints.
- [ ] T031 [US1] Build the accessible student chat form, session-only state and all outcome rendering in `frontend/src/features/chat/StudentChat.tsx`, `frontend/src/features/chat/chatState.ts`, and `frontend/src/styles/accessibility.css`; use memory/session storage only, clear at session end, display descriptive citations/context and prominent emergency contacts, and provide labels, visible focus, keyboard access, contrast and live announcements
- [ ] T032 [US1] Document and exercise local database population in `README.md`: environment/model configuration, supported document formats, Docker Compose build/start, Alembic upgrade, mounted approved manifest, the ingestion/rebuild command, test-only seeding versus live approval, corpus/index inspection and representative retrieval/citation checks; run T018–T020 against the real PostgreSQL fixture corpus

**Checkpoint**: The complete RAG workflow works from approved input through verified student response. Shared safety/context gates remain enabled. This is the internal MVP validation point; complete remaining story and release acceptance checks before student launch.

## Phase 4: User Story 2 — Get Campus- and Program-Relevant Guidance (P2)

**Goal**: Ask focused follow-ups only when missing campus/program/course/term changes the answer, then give appropriately scoped guidance.

**Independent test**: Ask a campus-dependent question without campus, then with Hammond/Westville and program context. Verify the focused prompt and matching official catalog/prerequisite guidance, including current versus past academic terms.

### Tests

- [ ] T033 [P] [US2] Add material-context and `needs_context` contract tests in `backend/tests/unit/test_context.py` and `backend/tests/contract/test_context.py`; require non-empty requiredFields limited to campus/program/course/academicTerm and ensure irrelevant context is not requested
- [ ] T034 [P] [US2] Add end-to-end campus, program prerequisite and academic-term follow-up tests in `frontend/tests/e2e/context-guidance.spec.ts`, verifying a cited answer only after necessary context is supplied

### Implementation

- [ ] T035 [US2] Complete context-dependent catalog/prerequisite handling in `backend/app/services/decisions.py` and `backend/app/services/retrieval.py` using the representative campus/program/course/term cases; never present an inactive or unspecified-term deadline as current
- [ ] T036 [US2] Complete focused follow-up submission and applied-context labels in `frontend/src/features/chat/StudentChat.tsx` and `frontend/src/features/chat/chatState.ts`; retain context only for the session and preserve keyboard focus/live announcements
- [ ] T037 [US2] Run T033–T034 and the US1 regression tests, recording campus/program/term acceptance results in `specs/001-pnw-student-chatbot/validation-results.md`

**Checkpoint**: Context guidance is independently verified without weakening the grounded-answer or safety paths.

## Phase 5: User Story 3 — Receive a Safe Referral (P3)

**Goal**: Verify safe outcomes for unsupported/personal/conflicting questions and provide authorized reviewers with practical source governance tools.

**Independent test**: Submit unsupported, ambiguous, account-specific, unreadable, conflicting and imminent-danger messages. Verify explicit limitations, correct official referrals or immediate emergency contacts. Retire/supersede a cited source and confirm new answers stop using it within one hour.

### Tests

- [ ] T038 [P] [US3] Add safe-outcome and reviewer list/create/status contract tests in `backend/tests/contract/test_safety_and_review.py`, plus failure/retirement integration tests in `backend/tests/integration/test_safety_and_retirement.py`; cover AI timeout/unavailability, malformed output, 401/403/409, non-empty review reasons, revision approval/conflicts, and 100% exclusion from new answers within one hour after retirement or supersession, including in-flight generation and repeat imports
- [ ] T039 [P] [US3] Add end-to-end referral/unresolved/emergency and reviewer tests in `frontend/tests/e2e/safety-and-review.spec.ts`; verify appropriate contacts, no individual determinations, keyboard behavior, and emergency responses without normal retrieval/generation

### Implementation

- [ ] T040 [US3] Expose authorized reviewer GET/POST `/api/v1/reviewer/sources` and PATCH `/api/v1/reviewer/sources/{sourceId}/status` in `backend/app/api/routes/reviewer_sources.py` and `backend/app/api/schemas/reviewer.py`; reuse foundation governance, return the documented status codes/source fields, and expose effective context, revision history, conflicts and audit data needed by the reviewer UI in accordance with `specs/001-pnw-student-chatbot/contracts/api.md`
- [ ] T041 [US3] Build a simple reviewer page with PNW OIDC sign-in, source draft creation, status actions and revision/history display in `frontend/src/features/reviewer/ReviewerSources.tsx` and `frontend/src/app/routes.tsx`; keep approval/retirement with the owning office and escalated conflict resolution with Dean of Students
- [ ] T042 [US3] Complete referral directory selection and unresolved/emergency presentation across representative topics in `backend/app/services/decisions.py` and `frontend/src/features/chat/StudentChat.tsx`; require active appropriate official contact URLs, transparent limitations, and published PNW safety contacts, without generating decisions about personal records
- [ ] T043 [US3] Run T038–T039 and all story regressions, exercise pending-revision approval → ingestion → activation → retirement using the CLI/reviewer UI, and record source-governance, safe-failure and retirement timing evidence in `specs/001-pnw-student-chatbot/validation-results.md`

**Checkpoint**: All three stories and the complete source lifecycle are independently verified on the shared implementation.

## Phase 6: Release Validation and Documentation

**Purpose**: Verify every specification success criterion with practical tests and recorded human review.

- [ ] T044 [P] Add a review dataset in `backend/tests/fixtures/acceptance_questions.json` and runner in `backend/tests/acceptance/test_review_set.py`: at least 100 supported questions with 100% relevant approved citations and source/context agreement (SC-001/002), at least 30 unsupported/conflicting/account-specific questions with 100% limitations/appropriate referrals (SC-003), at least 90% campus-correct answers/follow-ups (SC-006), and 100% immediate emergency guidance/PNW contacts across danger/self-harm/violence indicators (SC-011); include source-backed human review of actual generated answers
- [ ] T045 [P] Add privacy/security tests in `backend/tests/integration/test_privacy_security.py` and `frontend/tests/e2e/session-privacy.spec.ts`: no question/student identity/prompt/transcript in DB or normal logs, 100% identifiable conversations unavailable after session end (SC-009), public student access, OIDC office boundaries, CORS/rate limits, no-store, privacy-safe errors, HTTPS source URLs and secret/dependency/image checks
- [ ] T046 [P] Add axe-core/keyboard/focus/live-region tests in `frontend/tests/a11y/chat.a11y.spec.ts` and conduct manual screen-reader/contrast/WCAG 2.2 AA review, recording results in `specs/001-pnw-student-chatbot/accessibility-review.md`; both automated and manual checks must pass before launch (SC-008)
- [ ] T047 [P] Add and run a representative supported-question load test in `backend/tests/performance/test_chat_latency.py` with the configured provider under normal service; require at least 95% within 10 seconds (SC-010), record measurements, and only if necessary add/validate a compatible cosine HNSW index in `backend/alembic/versions/0002_vector_index.py` without weakening eligibility or retrieval quality
- [ ] T048 [P] Conduct student usability validation and record only anonymized aggregate findings in `specs/001-pnw-student-chatbot/usability-review.md`; at least 85% must find a source-backed answer/referral within three minutes without independent webpage navigation (SC-004), and at least 80% must rate clarity/source usefulness satisfactory or better (SC-005)
- [ ] T049 Finalize setup, source approval/import/update/rebuild, supported formats, failure recovery and test instructions in `README.md` and `specs/001-pnw-student-chatbot/quickstart.md`; run the documented Docker Compose pytest/Vitest/Playwright/accessibility commands and record actual results, acceptance/latency measurements and remaining blockers in `specs/001-pnw-student-chatbot/validation-results.md`
- [ ] T050 Review implementation, tests and evidence against every FR/SC and constitution principle in `specs/001-pnw-student-chatbot/review.md`; record traceability, security/privacy/documentation impact and any explicitly approved exceptions, and leave failed or unperformed checks incomplete rather than claiming launch readiness

## Dependencies and Parallel Work

The default is the listed order: **Setup → Foundation → US1 (P1) → US2 (P2) → US3 (P3) → Release checks**. No task requires a later task to implement its behavior. Story tests are authored first and pass at their story checkpoint. Each story is independently testable on the preceding shared implementation.

- **Setup**: T001 first; T002–T004 may run together, then T005.
- **Foundation**: T006–T010 establish schema/fixtures; author T011, implement T012–T017, and pass foundation tests. Models precede the single owning schema migration, avoiding duplicate migrations. Governance services are available to ingestion before the reviewer HTTP/UI tasks.
- **US1**: After Foundation, author T018–T020 concurrently in separate files. Implement T021–T027 in order, then T028–T031 and verify/document in T032. Approval remains a human owning-office action: initial local validation uses explicitly test-only fixtures; live ingestion requires recorded approval.
- **US2**: After US1, T033 and T034 can be authored concurrently; T035–T037 then proceed in order. They complete and validate the shared context behavior rather than introducing a second classifier.
- **US3**: After US2, T038 and T039 can be authored concurrently; T040–T043 proceed in order. They expose existing governance and validate existing safety behavior rather than introducing duplicate services.
- **Release**: T044–T048 can run in parallel after the story checkpoints, using separate result files or in-memory test reports. T049 consolidates results and documentation; T050 reviews them last. Do not overlap tasks editing the same shared service, UI or results document.

`[P]` means different files and no unfinished prerequisites within that group. It permits parallel work; it does not require multiple agents or extra infrastructure.

## RAG Workflow and Requirement Coverage

```text
Approved PNW sources → validate source/revision approval → extract text
→ clean/normalize → chunk → generate embeddings
→ PostgreSQL/pgvector: source history + chunks + metadata + citations + vectors
→ approved/active/effective/context filtering before vector/full-text ranking
→ grounded AI generation → verify support/citations and recheck eligibility
→ student response (or focused follow-up / safe referral / unresolved / emergency)
```

| Requirements | Implementation and validation |
|---|---|
| FR-001–FR-004, FR-009 | Foundation approval rules; T018–T032 complete ingestion, grounded answers and official citations; T044 review set |
| FR-005, SC-007 | T013–T014 lifecycle/eligibility, T026 refresh, T038/T043 retirement/supersession tests: all new answers exclude affected material within one hour |
| FR-006–FR-007, FR-011 | T016 material-context gates, T023/T028 scope metadata/filtering, T033–T037 contextual catalog guidance, T044 campus threshold |
| FR-008, FR-010, FR-012, FR-016 | T015–T016/T030 safe outcomes, T038–T043 failure/emergency/referral checks, T044 safety thresholds |
| FR-003, FR-013 | T007–T014 reviewed source history/office roles/audit, T040–T043 reviewer interface and lifecycle |
| FR-014–FR-015 | T012/T031 accessible session-only UX, T045 privacy, T046 automated/manual WCAG 2.2 AA |
| SC-001–SC-003, SC-006, SC-011 | T044 numerical review-set and safety acceptance |
| SC-004–SC-005, SC-008–SC-010 | T045–T048 retention, accessibility, performance and student usability |
| Constitution I–VIII | Approved requirements, small tested increments, simple architecture, reproducible setup, grounding/fail-safe gates, and T049–T050 evidence/review; resolve material ambiguity with humans before affected implementation |

## Implementation Strategy

Deliver an internal MVP after Foundation and US1: a reproducibly ingested corpus, verified grounded answers, and mandatory context/safety gates. Complete US2 and US3 validation and reviewer tools next. Launch only after the full release checks, including human accessibility/usability review, pass or a permitted exception is explicitly approved under the constitution. Keep ordinary tests offline with fixtures; do not substitute mocked results for actual provider performance, source-grounding review or human usability evidence.
