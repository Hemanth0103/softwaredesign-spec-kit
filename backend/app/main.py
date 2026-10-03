"""Public API and application-owned database lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import configure_middleware
from app.api.routes.chat import router as chat_router
from app.config import load_settings
from app.db.session import create_db_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.settings = load_settings()
    settings = app.state.settings
    engine = create_db_engine(
        settings.database_url.get_secret_value(), settings.database_timeout_seconds
    )
    app.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
configure_middleware(app)
app.include_router(chat_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """A tiny 'I am running' response for Docker; database has its own probe."""
    return {"status": "ok"}
