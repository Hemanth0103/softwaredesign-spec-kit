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
types. T005 adds only the `/api/health` startup probe; application routes and
database sessions remain later tasks. For containers, see the
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
