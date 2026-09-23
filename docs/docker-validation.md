# T005 validation record

**T005 passed on 2026-09-23.** Only T005 was completed in this validation run.
T006 and later tasks were not implemented or marked complete.

Environment: Docker Desktop 4.92.0, Docker Engine 29.8.0, Docker Compose v5.5.1,
Linux ARM64 containers on an Apple silicon Mac.

## Results

| Check | Actual result |
|---|---|
| Pinned images | Python 3.12.14, Node 26.8.2, Caddy 2.11.4 and pgvector 0.8.6/PostgreSQL 17 downloaded/built successfully using the recorded digests. |
| API targets | Runtime, development and test targets built successfully. |
| Web targets | Runtime, development and test targets built successfully, including Chromium and Linux browser libraries. |
| Base startup | `up --build -d --wait` passed; DB and API became healthy before their dependents started. |
| Runtime routing | Website returned HTTP 200 at port 8080; Caddy forwarded `/api/health` unchanged and returned `{"status":"ok"}`. |
| Fresh database | Initialization installed pgvector 0.8.6 on a newly created volume. |
| Database connectivity | Python's installed SQLAlchemy/psycopg connected from the API container using its configured database URL and queried the test row. |
| Persistence | A temporary row survived `compose down` followed by container recreation; its test table was removed afterward. |
| Existing database | `CREATE EXTENSION IF NOT EXISTS vector` succeeded repeatedly without replacing existing data. |
| Isolation | Database network was internal; DB and API had empty host port bindings, including in production mode. |
| Development | Both development targets started; website and `/api/health` worked at port 5173 through Vite. Source mounts were verified read-only. |
| Backend tests | 23 pytest tests passed inside the final test container. |
| Backend tools | Ruff lint/format, mypy and `pip check` passed inside the test container. |
| Frontend tests | Vitest: 1 passed; Chromium: 1 passed; axe: 1 passed inside the final test container. |
| Frontend tools | Lint/type checks passed inside the test container; the production frontend build passed during image construction. |
| Compose checks | All 6 automated configuration tests passed using Compose v5.5.1. |
| Caddy | Final HTTP and HTTPS configurations passed `caddy validate`. |
| TLS termination | Production override started with hostname `localhost` and loopback test ports. HTTPS website/API probes passed using Caddy's local root certificate with curl certificate verification enabled. HTTP returned 308 redirect to HTTPS. |
| Runtime hygiene | API ran as UID 10001; no `.env*` files were present under its image's `/app`. |
| Migration job | Built and ran as a one-off container, then exited 1 with the documented missing-T006/T009 message. No application migrations were created. |

## Fixes found during actual Docker validation

- Gave the non-root API user ownership of `/app`. This removed pytest's cache
  permission warning and allowed lint/type-check caches to be written normally.
- Applied Caddy's canonical formatting, removing its configuration format warning.

The remaining two Python warnings are upstream TestClient deprecations already
present locally; they do not fail tests. No dependencies were changed.

## How this was tested

Validation used isolated Compose project names `pnw-t005-validation` and
`pnw-t005-tests`, with `.env.example` placeholder settings and new named volumes.
An additional temporary Compose override pointed API/migrate `env_file` at the
absolute `.env.example` path, leaving personal `.env` settings untouched.

The workflow was:

1. Build/start the base stack with `up --build -d --wait`.
2. Probe website/API, query pgvector and create a temporary persistence row.
3. Run `down` without `-v`, recreate the stack, verify the row, then remove its table.
4. Build/start the development override and probe website/API on port 5173.
5. Build the test override and use `run --rm --no-deps` for backend/frontend checks.
6. Run `run --build --rm migrate` and verify the expected status 1 and explanation.
7. Start the production override with `PUBLIC_DOMAIN=localhost`, mapping only
   `127.0.0.1:8081:80` and `127.0.0.1:8443:443` for the test. Copy Caddy's local
   root certificate from `/data/caddy/pki/authorities/local/root.crt` and use
   `curl --cacert` for HTTPS probes. No certificate was installed in the Mac's
   trust store and no public deployment was performed.
8. Stop/remove both validation projects with `down`, keeping volumes and cached
   images. No validation containers are left running.

See [the beginner guide](docker.md) for normal commands. The automated Compose
checks can be repeated with `python3 -m unittest discover -s infra/tests -v`.

## Scope limits

Successful application schema migrations require T006/T009. The T005 job's safe
failure is validated, rather than pretending migrations exist. Public-domain
certificate issuance still requires deployment DNS and reachable ports; local
TLS termination and certificate verification are validated here. These starter
page tests do not establish chatbot functionality or full WCAG compliance.
