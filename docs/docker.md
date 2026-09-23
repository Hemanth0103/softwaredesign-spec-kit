# Docker, explained from the beginning

This guide covers **T005: running the project in Docker**. The website is still a
starter page. The API currently only has `/api/health`, which says it is running.
Chat, source ingestion, login and database tables come in later tasks.

## What are we installing, and why?

Without Docker, you would install Python, Node.js, PostgreSQL and their dependencies
yourself. Docker packages those environments so everyone can run the same setup.
You still need Docker installed on your computer; Python/Node/PostgreSQL do not
need separate host installations for the Docker commands below.

| Word | Plain-language meaning |
|---|---|
| Image | A packaged environment, like a prepared recipe and ingredients. |
| Container | A running copy of an image. Stopping it does not delete the image. |
| Dockerfile | Instructions for building one image. |
| Compose | Instructions for starting the related containers together. |
| Service | A named part of the application: `web`, `api`, or `db`. |
| Port | A numbered connection point. `8080` is our local website's port. |
| Volume | Saved files that survive replacing a container, especially database data. |
| Network | A connection between containers. Our database network is private. |
| Health check | A repeated check that a service is responding. |
| Build target | A named version of an image for normal use, development, or tests. |
| Migration | A controlled update to database tables, run once for a release. |
| TLS / HTTPS | Encryption between the browser and the website. |

```text
Your browser → web (website and proxy) → api (Python) → db (PostgreSQL)
                   localhost:8080         private        private
```

The web service uses Caddy, a small web server, to serve React's built files and
forward `/api/...` requests to Python. This forwarding is called a reverse proxy.
The browser uses one address for both, so ordinary local use does not need CORS
exceptions. Only the API and migration job can connect to the database network.

## 1. Install Docker once

On your Mac, install [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/).
Choose Apple silicon for an M-series Mac, or Intel for an Intel Mac. Open Docker
Desktop after installation and wait for its engine to start. Docker Desktop
includes Docker Compose. This project requires **Compose 2.24.4 or newer**.

Open Terminal in this repository's folder and check:

```sh
docker version
docker compose version
```

`docker version` should show both Client and Server. If it says it cannot connect
to the daemon, open Docker Desktop and wait. Docker's “daemon” is its background
engine. Installing a command-line client alone does not start that engine.

## 2. Create your local settings

Run the following only if you do not already have `.env` (it must not overwrite
settings you have already entered):

```sh
cp -n .env.example .env
```

Open `.env` in your editor. Replace `replace-with-local-password` in **both**
`POSTGRES_PASSWORD` and `DATABASE_URL` with the same password. A randomly generated
letters-and-numbers password is simplest because URL punctuation needs encoding.
The other placeholders are enough to start this infrastructure-only version;
they will not connect to a real AI or identity provider. Replace them before
implementing/exercising those integrations. Do not share `.env` or commit it.

The hostname `db` in `DATABASE_URL` means “the database container,” not your Mac.
The database name and user are both `pnw`, matching `compose.yaml`.

`.gitignore` keeps local settings out of Git. `.dockerignore` keeps them out of
image builds. Compose passes settings to the API only when the container runs.
Never put keys into frontend code, `VITE_...` variables, or Docker build arguments.
Docker administrators can inspect runtime environment variables; use controlled
host access and your deployment's secret management for real credentials.

## 3. Build and start

Run all these commands from the **repository root**, where `compose.yaml` lives:

```sh
# Check configuration without printing your resolved passwords.
docker compose config --quiet

# Download the base images, install locked dependencies, and start the services.
docker compose up --build -d --wait

# Show what is running.
docker compose ps
```

The first build takes longer because Docker downloads images and dependencies.
`--build` prepares images; `-d` keeps containers running in the background;
`--wait` waits for started/healthy services. The database starts first, then the
API, then the website. DB and API should show `healthy`; web should show running.

Open **http://localhost:8080**. You should see the starter page. Open
**http://localhost:8080/api/health** to see `{"status":"ok"}`. This means the API
started with valid settings; it does not mean the chatbot or external providers
are working. The database has its own separate health check.

## 4. Check the database without exposing it

```sh
docker compose exec db psql -U pnw -d pnw -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

You should see a pgvector version. `exec` runs a command inside an already running
container; `psql` is PostgreSQL's command-line client. There is deliberately no
database port published to your computer or the internet.

The SQL file in `infra/postgres/init/` installs pgvector on a **new empty volume**.
Initialization files do not run again on an existing volume. If an older database
does not have the extension, apply this safe, repeatable command:

```sh
docker compose exec db psql -U pnw -d pnw -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Changing `POSTGRES_PASSWORD` in `.env` does not reset an existing database password.
Do not delete your database to fix this: restore the matching setting or arrange
a deliberate password change in PostgreSQL.

## 5. Stop and restart safely

```sh
# Stop containers while keeping them and their data.
docker compose stop

# Start them again.
docker compose start

# Or remove containers/networks while keeping database and certificate volumes.
docker compose down
```

After `down`, use `docker compose up -d --wait` to recreate containers. **Do not add
`-v` to `down` unless you deliberately want to delete saved data and certificates.**

## Development: see edits without rebuilding

Stop the normal stack first, then select the development override:

```sh
docker compose down
docker compose -f compose.yaml -f compose.dev.yaml up --build -d --wait
```

Open **http://localhost:5173**. This mode runs Vite instead of Caddy and reloads
changes in `frontend/src`, `frontend/index.html`, and `backend/app`. A mount makes
those local files visible inside the container. Dependency or configuration changes
still need a rebuild. Use the same two `-f` arguments when stopping this mode.

## Tests: run tools, then exit

The test override selects images that include pytest, Vitest and Chromium. It
does not launch an additional permanent service. Build these images first:

```sh
docker compose -f compose.yaml -f compose.test.yaml build api web
docker compose -f compose.yaml -f compose.test.yaml run --rm --no-deps api pytest
docker compose -f compose.yaml -f compose.test.yaml run --rm --no-deps web npm run test
docker compose -f compose.yaml -f compose.test.yaml run --rm --no-deps web npm run test:e2e
docker compose -f compose.yaml -f compose.test.yaml run --rm --no-deps web npm run test:a11y
```

`run` creates a temporary container; `--rm` removes it after the command finishes;
`--no-deps` avoids starting other services for the current standalone tests.
Later database integration tests need `db` running: omit `--no-deps` for those API
tests. Browser tests start their own Vite server **inside** their container.
No test port is exposed on your computer.

For configuration checks without starting containers, use a local Python:

```sh
python3 -m unittest discover -s infra/tests -v
```

These checks use `.env.example` placeholders and verify private networking, health
dependencies, secret separation, migration setup and all override combinations.
They do not prove that images build or containers run.

## Database migrations: available after T006/T009

T006 adds Alembic configuration and T009 adds the first application tables. T005
provides the one-off job, but **does not pretend those migrations already exist**:

```sh
docker compose run --build --rm migrate
```

For now this exits with an explanation of the missing tasks. Once the migrations
exist, the same job runs `alembic upgrade head` and exits. It reuses the API image.
For future releases: build images, start/wait for DB, run migration successfully,
then start/update API and web. Stop if migration fails. Do not run migrations
inside every API process or let multiple release jobs change the tables at once.

## Future deployment: HTTPS

Local HTTP is bound to your Mac's loopback address. Deployment uses a separate
override, not the dev/test overrides. On a deployment host, set `PUBLIC_DOMAIN`
in `.env` to a real hostname such as `chat.your-domain.edu` (no URL path).
Point that domain's DNS to the host, allow inbound TCP 80/443 (and optionally UDP
443 for HTTP/3), and allow Caddy to reach the certificate authority.

```sh
docker compose -f compose.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.production.yaml up --build -d --wait
```

Caddy obtains and renews TLS certificates and redirects HTTP to HTTPS. It forwards
API requests over the Docker network. Its certificate/config volumes must persist
between releases. This override publishes only web ports; DB/API stay private.
If your institution already terminates TLS at a managed proxy, coordinate with its
administrator instead of exposing a second public entry point.

This is deployment infrastructure, **not launch approval**: authentication,
privacy middleware, trusted proxy handling and the remaining application/security
tasks still need implementation and validation. The current API deliberately
ignores forwarded headers until T012 implements its trusted-proxy policy.

## Reading the files

| File | What it controls |
|---|---|
| `backend/Dockerfile` | Python environment and runtime/development/test stages. |
| `frontend/Dockerfile` | Node build tools, browser-test tools, and final Caddy image. |
| `compose.yaml` | Local three-service setup, readiness and saved data. |
| `compose.dev.yaml` | Live editing with Vite/Python reload. |
| `compose.test.yaml` | Temporary test containers. |
| `compose.production.yaml` | Public HTTPS ports and hostname. |
| `frontend/Caddyfile` | Website files and `/api` forwarding. |
| `infra/postgres/init/001-extensions.sql` | pgvector initialization. |
| `infra/migrate.sh` | One-off Alembic command with a clear missing-task error. |

The base images use exact version tags **and immutable SHA-256 digests**, verified
for Intel/AMD and ARM64. Update tags and digests deliberately when upgrading.
Python uses the existing hash-checked lock (including development tools for now).
Node uses `npm ci` with the existing lock. Browser-test OS libraries are installed
from Debian repositories at build time; those repositories are not snapshot-pinned.
Build/test tools and browsers are excluded from the final web image.

## Troubleshooting

| Message/symptom | What to do |
|---|---|
| `docker: command not found` | Install Docker Desktop, then reopen Terminal. |
| Cannot connect to Docker daemon | Open Docker Desktop and wait for the engine. |
| Unknown `!override` tag | Update to Compose 2.24.4 or newer. |
| Port already in use | Stop the process using 8080/5173; do not expose the DB to fix it. |
| Missing `.env` or required variable | Complete step 2 and check spelling. |
| DB password authentication failure | Ensure URL password matches the password used when the volume was created. |
| Unhealthy API | Read `docker compose logs --tail=50 api`; check required configuration. |
| Website says chatbot unavailable | Expected: T005 runs the starter, not the unfinished chatbot. |
| Migration says T006/T009 missing | Expected until those later tasks are implemented. |

Use `docker compose logs --tail=50 db api web` for startup troubleshooting. Do not
paste secrets or resolved `docker compose config` output into public messages.

Further reading: [Docker startup ordering](https://docs.docker.com/compose/how-tos/startup-order/),
[Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy/),
and [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
