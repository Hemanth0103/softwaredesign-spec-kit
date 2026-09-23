"""T005 startup probe; student/reviewer routes and middleware arrive in later tasks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import load_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Validate configuration before Docker calls the API healthy. This does not
    # contact AI/OIDC services or claim the unfinished chatbot is ready to use.
    app.state.settings = load_settings()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def health() -> dict[str, str]:
    """A tiny 'I am running' response for Docker; database has its own probe."""
    return {"status": "ok"}
