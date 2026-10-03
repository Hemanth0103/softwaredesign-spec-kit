# PNW Student Information Chatbot

The student chat, governed source ingestion, PostgreSQL retrieval, and verified
citation API are implemented. Reviewer screens and release acceptance checks are
still pending. The steps below populate an isolated local corpus and validate
the implemented answer workflow.

**New to Docker? Start with [the beginner's Docker guide](docs/docker.md).** It
explains the vocabulary, installation, each command, saved data, tests and HTTPS.
The Docker and Compose files also have plain-language comments beside the settings.

For development without Docker, see [backend setup](backend/README.md) and
[frontend setup](frontend/README.md). Feature requirements and planned tasks are
in [the feature folder](specs/001-pnw-student-chatbot/).

## Configure and start Docker Compose

Run from the repository root with Docker Engine and Compose v2 installed.
Copy `.env.example` to `.env` and replace the placeholders locally. Set matching
`POSTGRES_PASSWORD` and URL-encoded credentials in `DATABASE_URL`; the Compose
database hostname is `db`. Keep the environment file out of source control.

Configure `AI_PROVIDER`, `AI_API_KEY`, `AI_GENERATION_MODEL`,
`AI_EMBEDDING_MODEL`, `EMBEDDING_VERSION`, and `EMBEDDING_DIMENSION` together.
The dimension must match the embedding model output. `CHUNK_SIZE` and
`CHUNK_OVERLAP` currently count whitespace words (the older character comment in
`.env.example` does not describe the implemented chunker). A dimension or chunk
layout change needs a new embedding version. Set timeouts, browser CORS origins,
and actual PNW OIDC issuer/audience/office-role settings as described in
[backend configuration](backend/README.md).

Model names and a key alone do not install a provider: deployment supplies a
`deployment_ai:build` callable returning the `app.ai.Provider` protocol. Package
that module in the API image and register it in `app.state.ai_providers` through
a deployment application entrypoint, following
[the API registration example](backend/README.md#student-answer-api-t030).
The stock API fails safely with referrals when no transport is registered.
The local walkthrough below explicitly uses offline embeddings; it does not
require a working vendor key or establish live-provider quality or latency.

```sh
docker compose build api web migrate
docker compose up -d --wait db
docker compose run --rm migrate
docker compose run --rm --no-deps api alembic current
docker compose up -d --wait api web
curl --fail http://127.0.0.1:8080/api/health
```

The migration should report `0002_eligibility (head)`; repeating the upgrade preserves
data. The website is at `http://127.0.0.1:8080`. Only the web service publishes a
loopback port; PostgreSQL stays private. See the Docker guide for development
mounts and deployment TLS configuration.

## Populate an isolated local corpus

Use a **separate Compose project and environment file**, never the application or
production database. Create `.env.t032` from `.env.example`, setting the following
local-only values (also set a matching local `POSTGRES_PASSWORD`/`DATABASE_URL`):

```dotenv
AI_PROVIDER=test
AI_API_KEY=test-only-not-a-live-key
AI_GENERATION_MODEL=test-generation
AI_EMBEDDING_MODEL=test-embedding
EMBEDDING_VERSION=fixture-v1
EMBEDDING_DIMENSION=3
OIDC_ISSUER=https://identity.example.test
OIDC_AUDIENCE=test-api
```

This shell function consistently selects the isolated volume, runtime environment,
and test images. The test API image includes the fixture documents; the runtime
image deliberately omits tests. Leave any existing `.env` untouched.

```sh
export APP_ENV_FILE="$PWD/.env.t032"
t032() {
  docker compose --env-file "$APP_ENV_FILE" -p pnw-t032 \
    -f compose.yaml -f compose.test.yaml "$@"
}
t032 build api web
t032 up -d --wait db
t032 run --build --rm migrate
t032 run --rm --no-deps api alembic current
t032 run --rm -v "$PWD/backend/tests/fixtures/sources:/corpus:ro" api \
  python -m app.ingestion --manifest /corpus/cli-manifest.json \
  --environment test --seed-fixtures --deterministic
```

The checked-in CLI manifest references hash-locked Registrar/Public Safety HTML
and an academic-calendar PDF. These snapshots are local test evidence, not current
university approval or current deadlines. The scenario `manifest.json` in the same
directory has a different schema and is not CLI input. Supported extraction is
UTF-8 HTML, readable digital PDF, and simple DOCX; scanned/encrypted/unreliable PDFs,
unsupported tables or DOCX features, and other formats require manual conversion
and review. See [format limits](backend/README.md).

CLI success returns exit code 0 and `prepared=3`, with zero failures/review-required
revisions. Fixture seeding creates synthetic source/revision approval audits only
for absent identities. Prepared revisions are **approved, not active**, so they
cannot yet support answers. Activate these three test revisions explicitly:

```sh
t032 run --rm -T -v "$PWD/backend/tests/fixtures/sources:/corpus:ro" api python - <<'PY'
from pathlib import Path
from app.auth import Reviewer
from app.config import load_settings
from app.db.session import create_db_engine, create_session_factory
from app.ingestion.sources import load_manifest
from app.models.source import SourceRevision
from app.services.source_governance import review_source

settings = load_settings()
engine = create_db_engine(settings.database_url.get_secret_value())
factory = create_session_factory(engine)
manifest = load_manifest(Path('/corpus/cli-manifest.json'))
with factory.begin() as session:
    for entry in manifest.documents:
        revision = session.get(SourceRevision, entry.revision_id)
        if revision.status == 'active':
            continue
        review_source(session, reviewer=Reviewer('fixture:local-test-only',
                      frozenset({entry.owner_office})), source_id=entry.source_id,
                      revision_id=entry.revision_id, action='activate',
                      reason='Explicit local fixture activation; no live approval.')
engine.dispose()
PY
```

The fabricated reviewer in this snippet is for this isolated fixture database
only. Live source approval/activation must use a verified owning-office reviewer
and a recorded reason through `source_governance`; importing never grants either.
Reviewer HTTP/UI work is still pending. A live manifest must match those approved
source/revision UUIDs, canonical official HTTPS URLs, owner office, exact SHA-256,
media type, retrieval timestamp, and snapshot path. Mount the entire manifest
directory read-only; snapshot paths must remain inside it. Linked documents need
their own approval. With the packaged provider factory:

```sh
docker compose run --rm -v /absolute/path/approved-corpus:/corpus:ro api \
  python -m app.ingestion --manifest /corpus/manifest.json \
  --provider-factory deployment_ai:build
docker compose run --rm -v /absolute/path/approved-corpus:/corpus:ro api \
  python -m app.ingestion --manifest /corpus/manifest.json --rebuild \
  --provider-factory deployment_ai:build
```

Changed bytes register a pending-review revision and exit 1. Have the owning
office review its content/scope/dates, approve it, update the manifest UUID/hash,
prepare it, and explicitly activate it. The prior active revision remains usable
until superseded; repeat imports cannot restore retired material. For an embedding
rebuild of unchanged approved bytes, select a new `EMBEDDING_VERSION` as needed;
old immutable chunks and approval history remain intact.

Exercise an unchanged repeat import and rebuild locally:

```sh
t032 run --rm -v "$PWD/backend/tests/fixtures/sources:/corpus:ro" api \
  python -m app.ingestion --manifest /corpus/cli-manifest.json \
  --environment test --deterministic
t032 run --rm -v "$PWD/backend/tests/fixtures/sources:/corpus:ro" api \
  python -m app.ingestion --manifest /corpus/cli-manifest.json --rebuild \
  --environment test --deterministic
```

Both should reuse the same chunk IDs/count and add no approval events. To seed
referrals explicitly, add `--seed-referrals /corpus/referrals.json` with a separately
prepared JSON array in the format documented in the backend guide. The shipped
CLI manifest does not seed referrals; the foundation provides default contacts.

## Inspect the corpus and retrieval

```sh
t032 exec -T db psql -U pnw -d pnw -c "SELECT version(); SELECT extversion FROM pg_extension WHERE extname='vector';"
t032 exec -T db psql -U pnw -d pnw -c "SELECT status, count(*) FROM source_revision GROUP BY status;"
t032 exec -T db psql -U pnw -d pnw -c "SELECT embedding_model, embedding_version, embedding_dimension, count(*) FROM source_chunk GROUP BY 1,2,3;"
t032 exec -T db psql -U pnw -d pnw -c "SELECT count(*) AS chunks, count(*) FILTER (WHERE search_vector <> ''::tsvector) AS searchable FROM source_chunk;"
t032 exec -T db psql -U pnw -d pnw -c "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname;"
```

Expect three active revisions after activation, non-empty search vectors, the
configured three-dimensional `fixture-v1` embeddings, a GIN full-text index and
B-tree governance/scope indexes. Cosine vector search is exact; no HNSW index is
needed for this checkpoint. Counts alone do not establish eligibility or grounding.

Run actual hybrid retrieval against the mounted fixture database:

```sh
t032 run --rm -T api python - <<'PY'
from app.ai import AIAdapter, DeterministicProvider
from app.config import load_settings
from app.db.session import create_db_engine, create_session_factory
from app.services.retrieval import retrieve

settings = load_settings()
engine = create_db_engine(settings.database_url.get_secret_value())
adapter = AIAdapter(settings, providers={settings.ai_provider: DeterministicProvider()})
for question in ('Registrar', 'Public Safety', 'Spring Semester'):
    result = retrieve(create_session_factory(engine), question=question,
                      query_embedding=adapter.embed([question])[0], context={},
                      model=settings.ai_embedding_model, version=settings.embedding_version,
                      dimension=settings.embedding_dimension)
    assert result.chunks and not result.conflict_revision_ids
    print(question, [(chunk.canonical_url, chunk.citation_anchor) for chunk in result.chunks])
engine.dispose()
PY
```

The constant-vector substitute checks SQL/filtering/citation plumbing, not semantic
ranking quality. The tests below establish source-backed HTTP answers for synthetic
parking, add/drop, integrity, absence, standing, program prerequisites and document
tables, and reject irrelevant/invented citations, inactive deadlines and
context-incompatible evidence. Verification currently requires complete excerpt
text, so unsupported paraphrases produce an unresolved outcome.

## Run the T018–T020 checkpoint

The backend suite uses real PostgreSQL with test-only documents and deterministic
AI substitutes. Tests create isolated schemas/databases and clean up their own
artifacts; use only this dedicated test database. UI/browser tests use controlled
API responses and do not claim a browser-to-live-provider acceptance run.

```sh
t032 run --rm api sh -c 'export T006_TEST_DATABASE_URL="$DATABASE_URL"; \
  pytest tests/unit/test_ingestion.py tests/integration/test_ingestion.py \
  tests/contract/test_chat.py tests/integration/test_grounded_answers.py'
t032 run --rm web npm test -- tests/unit/chat.test.tsx
t032 run --rm web npm run test:e2e -- student-answer.spec.ts
```

The container shell exports the injected dedicated database URL for pytest.
For all implemented regressions, use `pytest tests`, `npm test`, `npm run
typecheck`, `npm run lint`, `npm run build`, and `npm run test:a11y` in the same
test targets. Missing `T006_TEST_DATABASE_URL` skips database tests and cannot
establish this checkpoint. See [T032 validation evidence](specs/001-pnw-student-chatbot/validation-results.md).

Stop the isolated services with `t032 down` and then `unset APP_ENV_FILE`. Keep
the named volume to inspect or resume the corpus; do not use `down -v` on data
you want to retain. Live-provider acceptance, latency, human accessibility and
student usability reviews remain required at their later tasks before launch.
