# Backend development

Supported runtime: **Python 3.12** (validated with 3.12.14).
Run these commands from `backend/`. The setup uses uv; the lockfile is also
compatible with pip's requirements format. uv 0.12.13 generated the initial lock.

```sh
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
source .venv/bin/activate
```

`pyproject.toml` declares runtime and development dependencies. `requirements.lock`
pins both sets, including transitive dependencies and distribution hashes, with
platform markers for local development and the future Linux Docker image.
Psycopg's binary extra supplies the PostgreSQL driver without a local libpq build.
HTTPX supports FastAPI's test client. Ruff provides linting/formatting; mypy checks
types. T005 adds only the `/api/health` startup probe; application routes remain later tasks. T006 provides database sessions and Alembic. For containers, see the
[beginner's Docker guide](../docs/docker.md).

From the activated environment:

```sh
ruff check .
ruff format --check .
mypy
pytest
```

Configuration tests cover environment loading, invalid settings, and secret
redaction. Run them with `pytest tests/unit/test_config.py`.

Runtime configuration is documented in the repository root `.env.example`.
Copy it to `.env`, replace the placeholders, and inject its variables into the
process as described there. `app.config.load_settings()` validates process
environment variables without automatically reading files or contacting services.
Database credentials and provider keys are masked in settings representations;
configuration errors report field names without supplied values. Keep secrets
out of logs, frontend variables, Docker build arguments, and image layers.

To regenerate the lock after an intentional dependency change:

```sh
uv pip compile pyproject.toml --extra dev --python-version 3.12 --universal \
  --generate-hashes --format requirements.txt --output-file requirements.lock
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
uv pip check --python .venv/bin/python
```

Existing pins are retained when possible. Add `--upgrade-package PACKAGE` to the
compile command for an intentional package upgrade. Commit the manifest and lock
changes together. Keep `.venv/` and secrets untracked.

## Database infrastructure (T006)

Create an engine at application/CLI startup with
`create_db_engine(settings.database_url.get_secret_value(), settings.database_timeout_seconds)`
from `app.db.session`, then call `create_session_factory(engine)`. Use
`with session_scope(factory) as session:` for a transaction that commits on success,
rolls back on failure, and always closes. Dispose the engine at shutdown. Imports
open no connections. Engines enforce UTC, connection/statement timeouts, and hide
SQL parameter values. Do not log database URLs or connection exceptions containing secrets.

Models inherit `Base` and opt into `UUIDMixin` and `TimestampMixin` from
`app.db.base`. UUIDs and aware UTC timestamps are assigned at insert/flush;
`updated_at` advances on SQLAlchemy updates. Direct SQL updates must explicitly
set `updated_at`. Append-only records should declare only their own event timestamp.

With `DATABASE_URL` (and optionally `DATABASE_TIMEOUT_SECONDS`) injected into the
process, run from `backend/`:

```sh
alembic upgrade head
alembic current
alembic upgrade head --sql
```

Migrations do not require AI or OIDC settings. From the repository root,
`docker compose run --build --rm migrate` uses the same configuration against
both fresh and existing database volumes; retain the volume when upgrading.
Alembic records applied revisions and repeated upgrades apply only pending work.
T006 has no application schema revisions: T007–T009 add model imports to
`alembic/env.py` and the initial schema revision. Never use `create_all` or stamp
an existing schema as a substitute for reviewed migrations.

Validation (2026-09-27): all 28 backend tests passed in the Docker test image
against PostgreSQL 17.11 / pgvector 0.8.6 (Docker Engine 29.8.0). Ruff lint/format
and mypy passed. The isolated PostgreSQL volume passed fresh migration, repeated
upgrade, UUID/UTC round-trip, timeout configuration, commit/rollback, and persisted
row checks after a database restart. The shipped migration entrypoint also passed
twice on a separate empty database (T006 has no domain revisions yet).

The opt-in PostgreSQL test generates a temporary probe revision. Run it only against
a dedicated disposable validation database, never the application database:

```sh
T006_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/TEST_DB \
  pytest tests/integration/test_postgres.py
```

Restart that database without deleting its volume, then repeat with
`T006_EXPECT_PERSISTED=1` to require the previous row before rerunning migrations.
The test intentionally retains its probe table and Alembic version for this check.
Without `T006_TEST_DATABASE_URL`, the test skips. SQLite migration tests and offline
PostgreSQL SQL generation (including percent-encoded credentials) remain available
without Docker.


## Source and revision models (T007)

`app.models.source` defines `ApprovedSource` and `SourceRevision`, registered with
Alembic metadata. Sources have unique HTTPS URLs, office/subject metadata, default
`draft` approval and `active` lifecycle. Revisions retain source relationships,
SHA-256 hashes, aware UTC retrieval/effective timestamps, campus, and optional
program/course/academic-term scope. Effective date windows must be ordered.

New revisions start `pending_review`; assign `status` through `approved` →
`active` → `superseded` or `retired`. Terminal revisions cannot reactivate.
ORM assignments reject hash changes and invalid transitions, including after
reloading a record. Use ORM instance writes for these operations: bulk/direct SQL
bypasses Python validators. Database constraints cover required fields, source URL
uniqueness, status/campus values and effective windows. Reviewer authorization and
atomic activation/supersession belong to T013; the deployed schema migration
remains T009. No new dependencies were needed.

T007 validation: 48 tests passed in the Docker test image with PostgreSQL enabled;
Ruff and mypy passed. Source model tests use isolated PostgreSQL schemas and remove
them afterward. Run `pytest tests/unit/test_source_models.py`; supply the dedicated
`T006_TEST_DATABASE_URL` above to also execute its PostgreSQL cases.


## Chunks and review events (T008)

`SourceChunk` stores revision-linked text, zero-based ordinal, optional heading,
table context and citation anchor, PostgreSQL `tsvector`, pgvector embedding, and
embedding model/version/dimension. Vector dimensions are checked against each row;
non-finite embeddings are rejected. Ingestion must supply the populated search
vector before insertion (T025); the empty default is not searchable content.

`SourceReviewEvent` stores source and optional revision IDs, reviewer OIDC subject,
action, non-empty reason, and UTC timestamp. Revision-level events must reference
the specified source. Inserting an event does not authorize an action: T013 will
enforce source-owning office permissions and Dean of Students conflict resolution.

Both models reject ORM updates and deletes after insertion. Use new records for
rebuilds and subsequent review actions. These model guards do not intercept direct
SQL or bulk DML; use ORM instance writes. Alembic registers both models; schema
migration and indexes remain T009. The SQLite session smoke test creates only its
own test table because the new models require PostgreSQL-native types.

Validation: all 58 tests passed in Docker against PostgreSQL/pgvector, including
vector distance/full-text round trips, immutable/append-only guards, dimension
mismatch, invalid review fields, and mismatched source/revision rejection. Ruff
lint/format and mypy passed. No new dependencies were required: SQLAlchemy,
Psycopg and pgvector were already installed from the pinned lockfile.

Run the focused checks against the dedicated validation database:

```sh
T006_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/TEST_DB \
  pytest tests/integration/test_chunk_review_models.py
```


## Initial schema and referrals (T009)

T009 now supplies `alembic/versions/0001_initial.py`; earlier notes saying that
schema deployment is pending describe the state at T006–T008. The migration
creates sources, revisions, chunks, review events, and referral directory entries.
It enables pgvector with `CREATE EXTENSION IF NOT EXISTS`, including on existing
volumes whose initialization scripts have already run. No approval data is seeded.

Referrals contain UUID, topic, office, optional HTTPS URL/phone/email (at least
one required), campus (`hammond`, `westville`, or `all`), active flag and UTC
timestamps. Models and migration include source URL uniqueness, revision
source/hash uniqueness, chunk revision/ordinal/model/version uniqueness, foreign
keys, full-text GIN and B-tree eligibility/scope/contact indexes. Vector search
remains exact; no HNSW or IVFFlat index is created. All timestamp columns use
PostgreSQL timezone-aware types, with application connections configured for UTC.

From the repository root with runtime environment configured:

```sh
docker compose run --build --rm migrate
```

Or from `backend/` with `DATABASE_URL` injected: `alembic upgrade head` and
`alembic current`. Repeat upgrades are safe. The database role must be able to
create the vector extension on first deployment. A downgrade to `base` removes
the application's tables and data; it intentionally retains the shared extension.
Existing databases created by ad hoc `create_all` calls or test probe revisions
are not automatically adopted or stamped; use a clean application database.

Validation: 65 backend tests passed in Docker/PostgreSQL, with no skipped tests.
The initial migration test creates a disposable database, checks extension
creation, exact model/schema agreement, indexes, constraints, preserved referral
data on repeat upgrade, downgrade and re-upgrade. The packaged migration
entrypoint also passed twice on the retained test volume, and `0001_initial`
plus all five tables survived PostgreSQL restart. Ruff lint/format and mypy passed.
No new dependencies were needed.

Run the focused migration test with a dedicated test connection whose role has
`CREATEDB`: `T006_TEST_DATABASE_URL=... pytest tests/integration/test_initial_schema.py`.
It removes only its uniquely named disposable database when finished. The older
T006 infrastructure tests use isolated temporary revision directories so their
probe migrations remain separate from the real application migration graph.


## Shared offline fixtures (T010)

See [the fixture guide](tests/fixtures/README.md) for provenance and usage. The
corpus contains three official PNW downloads and four labeled synthetic documents,
16 lifecycle/context/safety scenarios, and five sourced referral/emergency contacts.
The loader verifies SHA-256 snapshots, validates the manifest and permits only
explicit local/test use. Test approvals are synthetic and never live authorization.
Pytest exposes fresh `corpus`, fixed `corpus_clock`, and fake `test_environment`
fixtures without network calls or automatic database seeding.

Frontend Vitest now loads `tests/setup.ts` for DOM cleanup, storage clearing, timer
restoration and global/environment stub cleanup between tests. Validation passed:
72 backend tests in Docker/PostgreSQL, three frontend tests, Python Ruff/mypy and
frontend lint/typecheck. No new dependencies were needed.


## Foundation tests (T011)

The three T011 test files are authored; their upcoming Python interfaces and
requirement coverage are documented in [foundation-tests.md](tests/foundation-tests.md).
The focused suite currently has 4 passing tests, 63 failures and 4 setup errors
because T012–T016 services/schemas/middleware do not exist yet. All failures were
checked to be missing-module errors. They remain visible and must pass at the
foundation checkpoint. The prior 72-test suite still passes in Docker/PostgreSQL.
No production behavior or later-task implementation was added for T011.


## API foundation (T012)

`app.api.middleware.configure_middleware` installs exact-origin CORS, generic JSON
HTTP/validation/server errors, chat `Cache-Control: no-store`, and an anonymous
fixed-window rate budget. The public `/api/health` endpoint remains available;
student/reviewer routes remain later tasks. Validation errors return 400 without
input values. Unhandled exceptions return a generic 500 without forwarding the
exception to server logging. Middleware reads no request bodies and retains only
aggregate request/response counts, never student identities, IP histories, prompts
or transcripts. Keep Uvicorn access logging disabled as in the Docker commands.

Runtime options (also in `.env.example`):

- `CORS_ORIGINS`: JSON array of exact allowed origins; default empty.
- `RATE_LIMIT_REQUESTS`: positive requests per process/window, default 120.
- `RATE_LIMIT_WINDOW_SECONDS`: positive window length, default 60 seconds.
- `TRUSTED_PROXY_NETWORKS`: JSON array of explicit proxy IPs/CIDRs, default empty;
  wildcard and universal networks are rejected.
- `REQUIRE_HTTPS`: default false for local development; enable for TLS deployment.

For TLS termination, configure the actual reverse-proxy peer address/subnet and
set `REQUIRE_HTTPS=true`. Only a trusted peer's single `X-Forwarded-Proto` value
(`http` or `https`) affects the request scheme. Other forwarding headers are
stripped; keep Uvicorn `--no-proxy-headers` so it cannot override this boundary.
Insecure API requests are rejected rather than redirected with user input.
`/api/health` is exempt so private HTTP health checks still work. CORS preflight is
handled separately and does not consume the API budget.

The rate budget is shared by all users within one process; it is intentionally
not a per-user/IP or cluster-wide quota. Multiple workers each have their own
budget. Configure deployment capacity accordingly. Limits return generic 429 JSON
with `Retry-After`; normal/error/preflight chat responses all carry `no-store`.
Only aggregate status-class counters are retained in middleware memory.

Validation: Docker/PostgreSQL full-suite run had **90 passed, 63 failed**. The
remaining failures are T011 tests for the unimplemented T013–T016 services/schemas;
the four T011 middleware tests now pass. New tests cover exact CORS, validation
redaction, rate-limit enforcement/reset, health exemption, proxy spoofing, trusted
HTTPS and invalid settings. Ruff lint/format and mypy pass. No new dependencies
were required. Run focused tests with `pytest tests/unit/test_middleware.py`, plus
`pytest tests/contract/test_api_rules.py -k "not question and not outcomes"`.


## Reviewer authentication and governance (T013)

`app.auth.OIDCValidator` verifies RS256 signatures using only the deployment's
`OIDC_JWKS_URL`; it never follows token-supplied key URLs or HTTP redirects.
Verification requires issuer, API audience, subject, issued-at and expiration,
and checks not-before when present. JWKS fetches use the configured OIDC timeout
and a 256 KiB body limit. Keys are fetched on each verification to observe rotation;
there is no stale-key fallback. Tokens and claims are not logged or cached.

Configure the actual PNW provider's HTTPS JWKS endpoint, `OIDC_ISSUER`, and the
resource/API `OIDC_AUDIENCE` (not an interactive client's ID-token audience).
`OIDC_ROLES_CLAIM` defaults to `roles`, a list of strings. `OIDC_OFFICE_ROLES` is a
JSON mapping of provider-managed role values to exact source-owning office names,
for example `{"registrar-role":"Registrar","dean-role":"Dean of Students"}`.
The actual role identifiers must come from the deployment administrator; the
example grants no authority by itself. Missing JWKS configuration rejects tokens;
empty/unmapped roles grant no reviewer access. The synchronous FastAPI dependency
`require_reviewer` returns 401 for invalid credentials and 403 for no mapped office.
It is ready for the reviewer routes in T040; this task does not expose those routes.

Use `create_draft` and `review_source` inside the caller's transaction/session scope.
Creation always produces a draft without approval. Owning offices approve sources,
approve pending revisions, activate revisions, supersede or retire evidence.
Activation supersedes prior active revisions and records each change. Source row
locks serialize governance actions; savepoints ensure state changes and audit writes
succeed together. The service does not commit the caller's transaction. Reasons
must be non-empty, unauthorized actions cannot write audit entries, and terminal
sources/revisions cannot reactivate.

For `resolve_conflict`, only Dean of Students is allowed; the caller must select
the losing active revision to retire and supply the resolution reason. This does
not approve new content or resurrect retired evidence. Resolution of multiple
conflicting sources can call this operation for each losing revision in one caller
transaction. Detection/exclusion of conflicting evidence remains T014. Review
subjects are retained only in the required audit records, not ordinary logs.

Dependencies: added and installed PyJWT 2.15.0 with cryptography 50.0.1 (plus pinned
cffi/pycparser); moved existing HTTPX to runtime dependencies for JWKS access.
The hash-locked Docker install and local dependency compatibility check passed.
Validation: full Docker/PostgreSQL suite **118 passed, 54 failed**; remaining
failures are the not-yet-implemented T014–T016 modules. T013 tests cover signed
claims, bad signatures/algorithms, expiration, issuer/audience, role shape, unknown
roles, JWKS failure/rotation, office/Dean permissions, draft/approval/activation,
audit history and atomic rollback. Ruff lint/format and mypy passed. Authentication
was tested with generated RSA keys and a deterministic HTTP transport, not a live
PNW tenant whose deployment settings have not been provided.


## Shared source eligibility (T014)

`app.services.source_eligibility` supplies the pure `is_eligible` predicate,
`eligible_revisions` SQL query and `recheck_revisions` final database gate. Eligibility
requires approved/active sources, active readable revisions, no unresolved group,
valid effective dates and matching material scope. Effective intervals include the
start and exclude the end. Campus `all` and absent program/course/term scopes are
unrestricted; a scoped value requires matching context. Missing or naive timestamp
inputs fail closed. No retrieval cache or scheduler is introduced.

Migration `0002_eligibility` adds `readable` (default false), indexed `conflict_group`
and the `unresolved` revision status. Existing revisions remain in place but must
be validated readable by later extraction before use. Upgrade with
`docker compose run --build --rm migrate`. Downgrading this revision retires unresolved
revisions before removing their conflict metadata; it never reactivates them.

When ingestion/retrieval identifies conflicting active evidence, call
`mark_conflicting(session, revision_ids)` and commit. It atomically marks all
participants unresolved under a persistent group. This records a detected conflict;
it does not attempt to infer semantic contradictions from arbitrary text.
Ordinary review actions cannot clear groups or activate replacements around them.
`resolve_conflict_group` requires a verified Dean of Students reviewer and non-empty
reason, restores at most one still-approved/readable revision and retires the other
participants, appending an audit for each. It cannot revive a retired source.
The earlier T013 single-revision operation does not resolve a T014 group.

Later ingestion and answer services must call `recheck_revisions(factory, ids,
context=...)` immediately before publication/return and reject false results.
It uses a new database session/transaction, so stale ORM objects or earlier retrieval
results cannot hide a committed retirement/supersession. Do not substitute an old
transaction or cache. The check observes commits as of its database read; HTTP
sending cannot be atomic with a subsequent database commit. Integration into those
later pipelines remains their own tasks; no student answer endpoint is added here.

Validation: **141 passed, 35 failed** in the full Docker/PostgreSQL suite; remaining
failures are unimplemented T015–T016 tests. T014 covers corpus eligibility, material
scope mismatches, date/readability filtering, stale-reader retirement, persistent
conflicts, Dean-only resolution and prevention of retired-source restoration.
Migration validation covers existing rows, repeat upgrades, model/schema agreement,
downgrade/re-upgrade; the retained test volume reached `0002_eligibility` successfully.
Ruff lint/format and mypy passed. All needed dependencies were already installed.


## Chat contract schemas (T015)

`app.api.schemas.chat` defines strict `StudentQuestion`, `QuestionContext`,
`Citation`, `Contact` and the five distinct outcome models. `ChatResponse` is a
Pydantic discriminated union keyed by `outcome`. Unknown fields and incorrect
scalar types are rejected. Questions preserve their input and enforce 1–4,000
characters; context lengths are 160/32/80 for program/course/academicTerm, with
campus restricted to Hammond/Westville enum values. Optional context may be omitted
or null. Response text must contain a non-whitespace character; citations,
requiredFields and emergency contacts must be non-empty in their respective outcomes.

Python attributes use snake_case while validation/serialization use the contract's
camelCase aliases (`academicTerm`, `contextLabel`, `officeName`, `contactUrl`,
`appliedContext`, `requiredFields`). Use `model_dump(exclude_none=True)` or future
FastAPI routes with `response_model_exclude_none=True` to omit absent optional data.
Links must be HTTPS without embedded credentials. Source approval, domain authority,
relevance and grounding remain the later eligibility/citation checks; schema
validation alone does not establish those properties.

The existing T012 middleware handles malformed JSON and schema failures as generic
400 JSON, rate limits as 429, and invalid server outputs as generic 500. All chat
responses remain no-store, and input/exception contents are not echoed or logged.
The tests exercise these behaviors on test-only routes; the public student answer
endpoint remains T030.

Validation: all 41 API contract tests passed locally; the full Docker/PostgreSQL
suite had **178 passed, 20 failed**, with only the unimplemented T016 decision-gate
tests failing. Tests cover request boundaries/types, all five valid outcome
round trips, missing required fields, unsafe URLs, HTTP validation, response aliases
and private server errors. Ruff lint/format and mypy passed. Existing Pydantic and
FastAPI dependencies suffice; no new packages were needed.


## Shared safety and context gates (T016)

`app.services.decisions.evaluate_gates` runs emergency → personal-record referral
→ material missing context → evidence/provider failure checks. It returns a
schema-validated safe outcome or `None` to permit the next processing stage.
It never generates policy text, retrieves evidence, contacts a provider, logs
questions, or stores conversation data. Later orchestration must call it before
retrieval/generation and again with evidence/provider results; `None` is not an
approved answer and does not bypass citation verification.

The caller supplies only context fields whose absence materially changes the
answer. The gate asks one focused question for the first missing material field;
it does not ask for irrelevant context. Explicit `emergency` and `account_specific`
flags can raise safety requirements; false flags cannot override detected risk.
These flags and evidence status are internal application inputs, not public request
fields. English text indicators are conservative deterministic heuristics, not a
complete natural-language safety classifier. Broader acceptance testing and the
future orchestration remain necessary before release.

Personal questions about enrollment, degree, financial aid, discipline, housing,
registration and records receive limitations plus office referrals, never an
individual determination. Unsupported evidence and provider failure produce
referrals; unreadable, ambiguous and conflicting evidence produce unresolved
responses. Unknown evidence status fails closed. Conflicts refer to Dean of Students.

Contact selection accepts active official PNW HTTPS directory records, prefers a
matching campus, then campus `all`. Explicit entries for a topic override defaults,
including inactive entries; inactive or invalid entries are never selected. An
unavailable topic can refer to the general Dean office; if no safe general contact
exists, `DirectoryUnavailable` lets API middleware return its generic service error.
Emergency guidance always includes 911 even if directory data is unavailable.

Default office links were verified on 2026-09-27:
[Financial Aid](https://www.pnw.edu/financial-aid/),
[Housing](https://www.pnw.edu/housing/),
[Dean of Students](https://www.pnw.edu/dean-of-students/),
[Registrar](https://www.pnw.edu/registrar/), and
[Public Safety](https://www.pnw.edu/public-safety/).
Public Safety's 911 and (219) 989-2222 emergency numbers are also preserved in the
T010 official snapshot. No policy content is inferred from these contact defaults.

Validation: **all 212 backend tests passed in Docker/PostgreSQL**, including all
previously red T011 tests and 34 decision-gate cases. Tests cover emergency priority,
record limitations, correct office selection, inactive/campus contact handling,
focused context, evidence failures and response schema conformance. Ruff lint/format
and mypy passed. No new dependencies were required. T017 remains incomplete.

## Governed source collection (T021)

`app.ingestion.sources.collect_manifest(session, manifest_path, ...)` collects
reviewed source bytes for the later extraction/store pipeline. It does not crawl
links, extract text, generate embeddings, activate revisions, or commit. No new
dependencies are required; it uses the existing HTTPX, Pydantic and SQLAlchemy.
The ingestion CLI is deferred to T027.

Supply an explicit JSON manifest using existing database source/revision UUIDs and
the reviewed revision's SHA-256 digest. The following placeholders must be replaced:

```json
{
  "schema_version": 1,
  "documents": [
    {
      "source_id": "<governed source UUID>",
      "revision_id": "<approved revision UUID>",
      "canonical_url": "https://www.pnw.edu/registrar/",
      "owner_office": "Registrar",
      "sha256": "<reviewed lowercase SHA-256 digest>",
      "media_type": "text/html"
    }
  ]
}
```

For a local snapshot, additionally set `path` to a file relative to the manifest
and `retrieved_at` to its timezone-aware retrieval timestamp. Absolute paths and
paths/symlinks escaping the manifest directory are rejected. Each linked document
needs a separate governed entry and its own approvals. The fixture corpus manifest
in `tests/fixtures/sources/` is a different, test-only format; collection cannot use
its synthetic approval flags as authorization.

Collection requires an approved, active source, an approved or active revision,
and source/revision approval audit events written by the owning-office governance
service. It verifies URL, owner, source/revision association and reviewed hash
against database records, and blocks unresolved source conflicts. It rechecks
these records after I/O and locks them until the caller ends the transaction.
Future T025 publication must perform its own final eligibility check. Readability
and effective-date/context eligibility remain extraction/publication/retrieval gates;
collecting an approved revision does not make it eligible for student answers.

Only HTTPS `pnw.edu` or its subdomains on port 443 are accepted. Each redirect is
validated before following; the original governed canonical URL remains the citation
identity. HTML, PDF and DOCX media types are accepted for collection, and HTTP
content type must match the manifest. Extraction support belongs to T022.
Defaults are 10 MiB per document, three redirects and a 10-second timeout/deadline
per download. The manifest itself is limited to 1 MiB and 1,000 entries. Streams
are checked against the byte limit and elapsed deadline; a blocked operation can
last an additional operation timeout. Compressed HTTP transfers are rejected to
keep the byte bound explicit. Pass `timeout_seconds=settings.source_timeout_seconds`
when wiring the configured application; limits can be supplied explicitly.

Use the existing transaction helper with an initialized session factory:

```python
from pathlib import Path
from app.db.session import session_scope
from app.ingestion.sources import collect_manifest

with session_scope(factory) as session:
    collected = collect_manifest(
        session, Path("approved-manifest.json"),
        timeout_seconds=settings.source_timeout_seconds,
    )
```

Each result preserves source identity/title/office/subject, revision scope/effective
dates, fetched URL or snapshot path, retrieval timestamp, media type and actual hash.
Matching reviewed content is returned as bytes. Changed bytes register a new
`pending_review` revision with `readable=False`, or reuse the same source/hash record
on retry. These results have `review_required=True` and `content=None`; the caller
must commit the registration and obtain owning-office review before attempting
that revision with an updated manifest. Existing terminal revisions are never
reactivated. The collector never mutates the old hash, grants approval or publishes
content. On any exception, the caller must roll back the transaction; do not consume
partial work. Snapshot mounts and manifests are operator-controlled inputs.

Run from the repository root:

```sh
backend/.venv/bin/pytest backend/tests/unit/test_ingestion_sources.py backend/tests/integration/test_source_collection.py
backend/.venv/bin/ruff check backend/app/ingestion backend/tests/unit/test_ingestion_sources.py backend/tests/integration/test_source_collection.py
backend/.venv/bin/mypy backend/app
```

The integration tests need `T006_TEST_DATABASE_URL` pointing to a dedicated
PostgreSQL test database. They check pending registration visibility/idempotency,
rollback, and retirement committed by a separate connection during collection.

Docker validation (2026-09-29): all 62 focused T021 tests passed, including the
three PostgreSQL integration checks. The implemented backend regression suite
passed with 274 tests, no skips and two upstream TestClient deprecation warnings.
The deliberately red future-task suites (`test_chat.py`, unit/integration
`test_ingestion.py`, and `test_grounded_answers.py`) were excluded. Validation used
the isolated `pnw-t021-validation` Compose project with `.env.example` settings
and a dedicated `pnw_t021_test` database with pgvector enabled. Containers were
removed afterward while preserving volumes; application data was untouched.

## Document extraction (T022)

`app.ingestion.extract.extract_document(content, media_type=...)` accepts collected
bytes and returns deterministic frozen `ExtractedBlock` values with `text`,
`heading`, `table_context`, and `citation_anchor`. It performs no fetching,
governance changes, chunking, embedding, or publication. Callers must retain the
collector's source/revision/provenance metadata and apply the later publication gate.

Supported inputs are UTF-8 HTML, unencrypted digital PDFs, and simple DOCX
WordprocessingML documents. Beautiful Soup handles HTML structure and entities;
pypdf validates PDF content streams; pdfplumber supplies character coordinates
(all extraction dependencies are locked with hashes).
DOCX uses bounded ZIP reads and standard-library XML parsing with entity/DOCTYPE
rejection, requiring no additional document dependency.

HTML extraction removes navigation, scripts, forms, hidden content and structural
boilerplate, prefers the main content, and retains section hierarchy and existing
fragment identifiers. Tables keep explicit row boundaries, cell separators, and
captions. DOCX preserves paragraph/list-item wording, standard Heading1–Heading6
styles, bookmarks, and simple table rows. PDF extraction reconstructs rectangular clipping cells and aligned academic-year
columns using character coordinates, retaining empty cells, wrapped labels, and
page continuations with `page=N` citation locators. Overlapping cells, text crossing
column boundaries, and tables without reliable boundaries require manual review.
It does not infer semantic heading levels. No OCR or generated
replacement text is used. Whitespace and Unicode NFC normalization preserve dates,
course codes and policy wording; no summarization or dehyphenation is performed.

Extraction fails for the entire document with `ExtractionError` on empty,
unsupported, malformed or unreadable content, parser warnings, any unreadable PDF
page, encrypted PDFs, PDF images/embedded forms, merged/nested/inconsistent tables,
and DOCX embedded content, tracked changes, fields or referenced footnotes/endnotes.
These cases require manual conversion and owning-office review of the converted
bytes before ingestion. Decorative PDF images are also conservatively rejected.
Limits are 10 MiB input, 500 PDF pages, 40 MiB decoded PDF content per page, and
40 MiB declared expanded DOCX archive size with at most 1,000 entries. These bounds
are validation checks, not a process memory/time sandbox for hostile parser inputs.

Run the isolated extraction checks from `backend/`:

```sh
pytest tests/unit/test_extraction.py
pytest tests/unit/test_ingestion.py -k 'html or readable or unreadable'
```

The remaining T018 chunking/embedding cases intentionally remain red until
T023–T024. T022 does not establish full ingestion/publication or answer grounding.

T022 PDF correction validation (2026-09-29): 38 focused extraction tests and
10 existing ingestion extraction checks passed (14 future ingestion cases
deselected). Exact calendar regressions cover all five Spring date columns,
the Summer module label, continued/wrapped third-module rows, empty cells,
deterministic output, overlapping cells, and text crossing inferred columns.
Ruff and strict mypy passed. The implemented regression suite passed in the
isolated `pnw-t022-correction` Docker Compose project with PostgreSQL enabled:
312 passed, no skips, two upstream TestClient deprecation warnings. Future-task
suites `tests/contract/test_chat.py`, `tests/unit/test_ingestion.py`,
`tests/integration/test_ingestion.py`, and
`tests/integration/test_grounded_answers.py` were excluded from that regression
run; the supported extraction cases in unit ingestion were run separately.
No T023 or later implementation was added.

### Deterministic chunking (T023)

`app.ingestion.chunk.chunk_document(blocks, size=800, overlap=100,
revision_id=...)` accepts extracted blocks and returns immutable chunks with
contiguous zero-based ordinals. Pass `settings.chunk_size` and
`settings.chunk_overlap` for deployment configuration; budgets count whitespace
words, not model tokens. Each paragraph/section stays separate, and oversized
prose uses the configured word overlap without crossing headings or anchors.
HTML/DOCX tables that fit remain verbatim; larger tables repeat the caption and
first header row while keeping body rows complete. PDF calendar tables are grouped
by explicit academic-year header and semester, repeating both in every chunk,
including continuations across adjacent pages. Empty year columns remain empty;
a new header replaces the previous column schema. Header/semester provenance is
recorded in `table_context`; `citation_anchor` stays on the actual row page.
Only the extracted year-cell layout and explicit `Fall Semester`, `Spring Semester`
or `Summer` labels are supported for PDF table context. Missing/duplicate headers,
unknown sections, page gaps, heading changes, intervening prose, changed row widths,
or values in unlabeled year columns raise `ValueError` for manual review. Other
PDF table layouts also require review. No dates or years are inferred. Oversized
complete rows with context require a larger budget; increasing the budget does
not resolve ambiguous context.

Supply the collected revision ID to preserve identity on every chunk. Persistence
must attach that same `SourceRevision` through `SourceChunk.revision_id`; the
revision/source relationships retain canonical URL, title, owning office, campus,
program/course/term, effective dates and immutable content hash. Chunking performs
no approval, scope inference, embedding or publication. Those pipeline steps
remain separate tasks.

Validate from `backend/`: `.venv/bin/pytest tests/unit/test_chunk.py
 tests/unit/test_ingestion.py -k 'not embed'`.


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

Run the implemented backend regressions from the repository root:

```sh
backend/.venv/bin/pytest backend/tests \
  --ignore=backend/tests/contract/test_chat.py \
  --ignore=backend/tests/integration/test_ingestion.py \
  --ignore=backend/tests/integration/test_grounded_answers.py -k 'not embed'
```

### T024 AI adapter

`app.ai.AIAdapter(settings, providers={name: transport})` selects `AI_PROVIDER`
from an explicit registry. Each transport implements the `Provider` protocol:
`embed(texts, model, dimension, timeout_seconds)` and
`generate_grounded_answer(question, excerpts, context, model, timeout_seconds)`.
The keyword arguments are documented in `app/ai.py`. Deployment constructs its
chosen vendor transport with `AI_API_KEY`; no vendor or live model is assumed,
and an unknown provider fails closed. This increment supplies the provider-neutral
boundary, not a vendor SDK or live-provider acceptance evidence. Transport code
must disable sensitive logging, enforce network deadlines, return vectors in
input order, and instruct generation to use only the supplied excerpts as evidence
and treat excerpt text as data rather than instructions.

`adapter.embed_chunks(eligible_chunks)` prepares every supplied chunk, preserving
chunk metadata and attaching immutable model/version/dimension and vectors.
The standalone `embed_chunks(..., embed=callable, model=..., version=...,
dimension=..., timeout_seconds=...)` supports ingestion's existing test interface.
Missing/extra, non-numeric, non-finite, zero or incompatible vectors reject the
whole batch. `adapter.embed(texts)` uses the same identity for query embeddings.
The caller must filter eligibility before embedding; T025 owns transactional
publication and governance rechecks.

Generation accepts only internal `ApprovedExcerpt` values constructed from
approved retrieval, not request-supplied evidence. It returns an **untrusted draft**
with `answer` and non-empty `citations`; T029 owns claim/citation verification and
the final eligibility recheck. An excerpt object is an internal trust boundary,
not proof that a revision is still eligible. The adapter never queries the DB,
publishes answers, or logs question, excerpt, credential or provider error values.

`AI_TIMEOUT_SECONDS` bounds each caller's wait. At most four calls run per process;
a timed-out synchronous call cannot be forcibly stopped and retains its slot
until its transport finishes. Saturation fails closed, and daemon workers do not
hold up process shutdown. Therefore transports must also enforce I/O timeouts.
`DeterministicProvider` is an explicitly injected offline substitute; it is never
registered automatically or used as a production fallback.

Validate from `backend/`:
`.venv/bin/pytest tests/unit/test_ai.py tests/unit/test_ingestion.py`, `.venv/bin/ruff check app tests`, and
`.venv/bin/mypy app`. No new packages or settings are required.

### Transactional revision storage (T025)

`app.ingestion.store.ingest_revision(factory, revision_id=..., content=...,
media_type=..., embed=..., model=..., version=..., dimension=...,
chunk_size=800, chunk_overlap=100, timeout_seconds=6.0)` owns its database
transactions and returns the committed chunk UUIDs. Pass bytes from governed
collection and an existing reviewed revision; both source and revision must have
approval audit events. The source must remain approved/active and the revision
approved or active, readable, conflict-free and currently effective. Scope and
provenance stay on the revision/source relationships.

Extraction and all embeddings are prepared before the write transaction. Storage
locks source then revision, rechecks governance and stores every chunk, citation
field, English full-text vector and validated embedding together. Any failure
raises a privacy-safe `IngestionError` and rolls back the complete write. Provider
calls have a bounded timeout and occur without governance locks. Concurrent
imports serialize their final checks and reuse one committed set of chunk IDs.

Unchanged source/hash/model/version/dimension imports reuse existing embeddings.
A changed dimension or chunk layout under an existing model/version fails; use a
new embedding version for a rebuild. Chunks remain immutable. This function does
not approve or activate revisions; governance activation controls retrieval.
Use the T026 refresh workflow below for changed content and model rebuilds.

Run storage validation with a dedicated PostgreSQL database containing pgvector:

```sh
T006_TEST_DATABASE_URL=postgresql+psycopg://... .venv/bin/pytest \
  tests/integration/test_ingestion.py
```

### Review-first refresh and rebuild (T026)

`app.ingestion.refresh.register_revision(session, source_id=..., content=...,
retrieved_at=..., media_type="text/html", base_revision_id=None)` registers
changed bytes under a new immutable SHA-256 hash with `pending_review` status.
The source must be official, approved, active, audited and conflict-free. Pass
an aware retrieval timestamp and the actual supported media type (HTML, PDF or
DOCX). Registration uses the caller's transaction; commit on success or roll
back on failure. Concurrent registrations serialize on the source and reuse
one revision for the same hash.

Scope and effective dates come from an explicit reviewed base revision or the
single active revision, falling back to a single approved revision. An ambiguous
base requires `base_revision_id`. The office must review these inherited fields
along with the changed content before approval. Extraction establishes
readability; unreadable supported content remains pending and cannot be prepared.
For a pending hash previously registered by collection, registration establishes
readability from the matching bytes without changing scope, retrieval time or
approval. Existing approved, active and terminal hashes retain their state;
registration never reactivates them.

The workflow is explicit:

1. Register changed bytes (including establishing readability for collector-created
   pending revisions) and commit. The old active revision stays eligible.
2. The owning office calls `source_governance.review_source` with `action="approve"`
   for the pending revision and a non-empty reason; commit that review.
3. Call `refresh.rebuild_revision(factory, revision_id=..., content=...,
   media_type=..., embed=..., model=..., version=..., dimension=...,
   chunk_size=800, chunk_overlap=100, timeout_seconds=6.0)`. It reuses T025 storage
   and returns committed immutable chunk IDs. It never grants approval or activation.
4. The owning office explicitly calls `review_source` with `action="activate"`
   and the prepared revision ID. Its transaction supersedes the previous active
   revision and appends both supersede and activate audits together. Failure rolls
   back the entire transition; old chunks and review history remain intact.

For a model rebuild, skip registration/review when bytes and approval are unchanged
and call `rebuild_revision` with a new model/version identity. Old chunks remain
immutable; matching retries reuse IDs without embedding again. Dimension/layout
changes under an existing identity fail. Preparation rechecks hash, approval audits,
readability, conflicts, effective dates and lifecycle after embedding, so retirement
or rejection during a provider call cannot publish or reactivate material.

The synchronous manifest/`--rebuild` CLI remains T027. No scheduler, cache or new
service is introduced. The PostgreSQL command above exercises the complete
refresh/rebuild and ingestion suite.

### Synchronous ingestion CLI (T027)

Run from `backend/` with the documented environment variables and migrated
PostgreSQL database. The CLI uses the governed manifest format described above:

```sh
python -m app.ingestion --manifest /absolute/path/manifest.json --provider-factory deployment_ai:build
python -m app.ingestion --manifest /absolute/path/manifest.json --rebuild --provider-factory deployment_ai:build
```

`deployment_ai:build` is a deployment-supplied Python callable accepting `Settings`
and returning the `app.ai.Provider` interface. Package it in the API image; the
repository has no registered live vendor adapter. Provider credentials come from
settings. The command never silently substitutes an offline provider. `--rebuild`
prepares the configured model/version/dimension through the existing rebuild
service and retains old immutable chunks; change the embedding version when
changing dimensions or chunk layout.

Reuse the API image and mount the manifest directory read-only (all referenced
snapshots must remain inside it):

```sh
docker compose run --rm -v /absolute/path/corpus:/corpus:ro api python -m app.ingestion --manifest /corpus/manifest.json --provider-factory deployment_ai:build
```

Normal imports require previously approved source/revision identities and their
owning-office approval audits. Changed content registers a pending-review revision
and prints its ID; obtain office review and update the manifest revision ID/hash
before retrying. Preparation never activates a revision; use the governance
workflow above to activate it after successful preparation.

For an isolated local/test database only, explicitly opt into synthetic fixture
approval and offline embeddings:

```sh
python -m app.ingestion --manifest /absolute/path/fixture-manifest.json --environment test --seed-fixtures --deterministic --seed-referrals /absolute/path/referrals.json
```

Use the **governed ingestion manifest** schema, not the test corpus scenario
manifest under `tests/fixtures/sources/`. Supply stable source/revision UUIDs,
canonical PNW URLs, owners, SHA-256 hashes, relative snapshot paths and timezone-aware
retrieval timestamps. Fixture-created sources use the title/subject `Local/test
fixture`, campus `all`, and synthetic approval audits. They remain approved,
unactivated revisions. Existing source/revision state is never reapproved or
restored by a rerun. Use separate governed manifests/review workflows for scoped
campus/program/course/term acceptance cases.

Referral JSON is an array of objects with stable `id` UUIDs, `topic`, `office`,
`contact_url` (official PNW HTTPS), optional `campus` (`all`, `hammond`, `westville`),
`phone` and `email`. Example:

```json
[{"id":"d9823027-55e3-469f-a04e-77c54eaf0727","topic":"registration","office":"PNW Registrar","contact_url":"https://www.pnw.edu/registrar/"}]
```

Referral seeding is explicit and local/test only. Source/referral seeding commits
as one transaction; invalid input rolls it all back. Reruns reuse matching IDs,
reject changed fixture metadata and preserve inactive referrals. A local/test
flag is an operator assertion: point it only at a dedicated non-production
database. Neither flag is enabled by default.

Output reports prepared revisions, chunk IDs returned (including reused chunks),
review-required revisions, failures and newly seeded revisions/referrals. Exit
codes: `0` all requested preparation succeeded; `1` operational/preparation failure
or pending office review; `2` invalid command usage. Collection is transactional;
preparation commits each revision atomically and continues after individual
preparation failures. Successful revisions remain committed for safe retries.
Errors omit exception details, credentials and document bodies. Check manifest,
approval audits, extraction support, embedding identity, provider and database
availability as indicated.

Validate from the repository root (set `T006_TEST_DATABASE_URL` to a dedicated
PostgreSQL database with pgvector to execute integration tests):

```sh
backend/.venv/bin/pytest backend/tests/unit/test_ingestion_cli.py backend/tests/integration/test_ingestion_cli.py
```
# Generated-answer verification (T029)

`app.services.citation_verifier.verify_answer(factory, draft=..., retrieval=...,
context=..., now=...)` returns a validated `SupportedAnswer` or raises the
privacy-safe `CitationVerificationError`. The chat service calls it immediately
before returning an answer and selects a safe unresolved outcome on failure.

The verifier accepts the AI adapter's `{answer, citations}` draft with
`{"chunkId": "UUID"}` citations, or the strict public supported-answer shape
with exact canonical URL/title citations. It constructs public citations from
retrieved metadata and applied context from trusted request context; supplied
applied context must match. Provider-supplied context labels are rejected.
Relevant retrieval conflicts, missing evidence, unknown fields, invented links,
irrelevant citations, and unsupported claims fail closed.

Support is intentionally conservative: the complete answer must consist of
complete cited excerpt texts, with whitespace normalization only. Multiple
complete excerpts can be joined with whitespace. Paraphrases, partial excerpts,
and altered dates/conditions/negation are rejected because text similarity alone
cannot prove meaning. This limits answer fluency; it is not a semantic entailment
checker or a substitute for governed source review. It preserves table/header
context by requiring complete chunk text.

A fresh database transaction checks cited chunk/revision/source identity,
content, title, URL, scope and section metadata against eligible approved, active,
readable, nonconflicting, effective revisions. Retirement and scope changes during
generation therefore block return. A concurrent commit after that final read is
outside the database/HTTP boundary. No drafts, questions or transcripts are stored
or logged by this module.

Validate with a dedicated pgvector database configured through
`T006_TEST_DATABASE_URL`:
`backend/.venv/bin/pytest backend/tests/unit/test_citation_verifier.py backend/tests/integration/test_citation_verifier.py`.

## Student answer API (T030)

`POST /api/v1/chat/answers` is public and accepts the strict student question/context
contract. Responses are validated as one of `answer`, `needs_context`, `referral`,
`unresolved`, or `emergency`, with `Cache-Control: no-store`. Existing middleware
returns private 400/429/500 errors; no exception text or submitted content is logged.
The application creates its database engine/session factory at startup without
connecting, and disposes the engine at shutdown.

`ChatService` checks emergency indicators before database or AI access, using the
foundation's published emergency contacts. It resolves normal referrals against
the governed directory (including inactive rows so defaults cannot override a
disabled contact). A database failure receives the private 500 response. An empty
directory uses the foundation's official default contacts.

Before embedding, a conservative PostgreSQL lexical probe checks approved,
active/effective, readable source sections for missing restricted campus, program,
course, or term scope. It filters supplied context and asks one focused question
at a time; universal sections require no context. This probe does not infer context
from question text and can over-request context on broad lexical matches. It never
supplies evidence to the generator. Normal retrieval then applies all eligibility
and embedding identity predicates. Conflicts block generation; empty retrieval,
embedding/generation failures, and timeouts produce a safe referral. Failed draft
verification produces an unresolved result. Final verification uses a fresh clock
and database read, preserving the strict complete-excerpt requirement above.

Deployments must explicitly register their provider transport. For example, a
deployment-owned `deployment_app.py` can export:

```python
from app.config import load_settings
from app.main import app
from deployment_ai import build

settings = load_settings()
app.state.ai_providers = {settings.ai_provider: build(settings)}
```

Run that module with `uvicorn deployment_app:app --host 0.0.0.0 --port 8000`
(or override the API container command accordingly). `build(settings)` follows
the same provider protocol as the ingestion CLI's `--provider-factory`; the
transport must enforce timeouts and disable sensitive logging. No vendor is
selected automatically and no deterministic provider is registered by default.
Missing provider registration yields a referral for normal questions; health,
emergency guidance, account referrals, and lexical context checks remain available.

Tests may inject `embed`, `generate_grounded_answer`, and `now` into `ChatService`
or override the route's `get_chat_service` dependency. Both AI stages are bounded,
including injected callables. An uncooperative timed-out transport can continue in
the existing bounded daemon executor, but its late draft cannot reach verification
or the student response. Questions, contexts, and drafts stay request-scoped and
are never written to storage.

Validate with a dedicated pgvector database through `T006_TEST_DATABASE_URL`:
`backend/.venv/bin/pytest backend/tests/unit/test_chat_service.py backend/tests/contract/test_chat.py backend/tests/integration/test_grounded_answers.py`.
