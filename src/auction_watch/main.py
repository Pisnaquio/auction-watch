"""FastAPI entry point and application lifecycle."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from auction_watch import __version__
from auction_watch.config import Settings, get_settings
from auction_watch.persistence.database import Database
from auction_watch.persistence.migrations import upgrade_head
from auction_watch.persistence.repository import ProfileRepository

logger = logging.getLogger(__name__)


def _web_dist() -> Path:
    configured = os.environ.get("AW_WEB_DIST")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application without opening SQLite during import."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime_settings = settings or get_settings()
        database: Database | None = None
        try:
            try:
                database = Database.open(runtime_settings.data_dir)
                upgrade_head(runtime_settings.data_dir, database.engine)
            except Exception as exc:
                logger.error("database initialization failed (%s)", type(exc).__name__)
                if database is not None:
                    database.dispose()
                    database = None
                application.state.database = None
                application.state.profile_repository = None
            else:
                application.state.database = database
                application.state.profile_repository = ProfileRepository(database)
            yield
        finally:
            if database is not None:
                database.dispose()
            application.state.database = None
            application.state.profile_repository = None

    application = FastAPI(title="Auction Watch", version=__version__, lifespan=lifespan)
    web_dist = _web_dist()
    if web_dist.is_dir() and (web_dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

    @application.get("/api/v1/health")
    def health() -> dict[str, Any]:
        """Confirm that the process is alive without consulting SQLite."""

        return {"ok": True, "service": "auction-watch", "version": __version__}

    @application.get("/api/v1/readiness")
    def readiness(request: Request) -> JSONResponse:
        """Confirm that SQLite is migrated and can answer a simple query."""

        database = getattr(request.app.state, "database", None)
        ready = database is not None and database.check_ready()
        payload = {"ok": ready, "service": "auction-watch", "version": __version__}
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @application.get("/", include_in_schema=False)
    def index() -> Response:
        """Serve the compiled frontend when it is available."""

        index_file = _web_dist() / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse({"service": "auction-watch", "version": __version__})

    return application


app = create_app()


def run() -> None:
    """Run the application with the configured host and port."""

    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
