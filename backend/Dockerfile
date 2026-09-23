# Build from the REPOSITORY ROOT so the root .dockerignore protects secrets:
# docker build -f backend/Dockerfile --target runtime .
# An image is a packaged environment. Each named stage below is a build target.
# The digest after @ pins the exact image contents, including its OS packages.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Install the same hash-checked lock used by local uv setup. No credentials are
# needed here. A separate environment can be copied into the final image.
COPY backend/requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
# Use an ordinary user rather than administrator privileges inside the API.
# Give that user ownership of the working folder too, so test/type-check caches
# can be written without administrator privileges.
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app && chown app:app /app
COPY --chown=app:app backend/ /app/
COPY --chown=app:app infra/migrate.sh /usr/local/bin/migrate
USER app
EXPOSE 8000

# Development reloads Python files when you edit the mounted app directory.
FROM base AS development
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--no-access-log", "--no-proxy-headers"]

# A test target is a packaged test environment, not a fourth running service.
FROM base AS test
CMD ["pytest"]

# The last stage is the normal image. Reuse the existing combined lock rather
# than maintain a second dependency list; it currently includes Python test tools.
FROM base AS runtime
# Tests are kept only in the development/test stages above.
USER root
RUN rm -rf /app/tests
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
