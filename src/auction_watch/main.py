"""FastAPI entry point and application lifecycle."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from auction_watch import __version__
from auction_watch.async_ops import NotificationRepository, RunQueueRepository
from auction_watch.config import Settings, get_settings
from auction_watch.notifications.sender import SMTPNotificationSender
from auction_watch.notifications.service import NotificationPlanner
from auction_watch.persistence.database import Database
from auction_watch.persistence.migrations import upgrade_head
from auction_watch.persistence.operational_repository import OperationalRepository
from auction_watch.persistence.repository import ProfileRepository
from auction_watch.profiles.seed import consoles_profile
from auction_watch.runner import AuctionRunEngine
from auction_watch.server.profiles import router as profiles_router
from auction_watch.worker import AuctionWatchWorker, NotificationDeliveryWorker, RunWorker

logger = logging.getLogger(__name__)


def _web_dist() -> Path:
    configured = os.environ.get("AW_WEB_DIST")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(
    settings: Settings | None = None,
    run_engine_factory: Any = AuctionRunEngine,
) -> FastAPI:
    """Create an application without opening SQLite during import."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime_settings = settings or get_settings()
        database: Database | None = None
        worker_thread: Thread | None = None
        stop_worker = Event()
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
                application.state.profile_repository.seed_system_profile(consoles_profile())
                application.state.operational_repository = OperationalRepository(database)
                application.state.run_engine = run_engine_factory(
                    database,
                    profile_repository=application.state.profile_repository,
                    operational_repository=application.state.operational_repository,
                )
                queue = RunQueueRepository(database)
                notifications = NotificationRepository(database)
                sender = SMTPNotificationSender(
                    host=runtime_settings.smtp_host,
                    port=runtime_settings.smtp_port,
                    sender=runtime_settings.smtp_sender,
                    recipient=runtime_settings.smtp_recipient,
                    username=runtime_settings.smtp_username,
                    password=runtime_settings.smtp_password,
                    use_tls=runtime_settings.smtp_use_tls,
                )
                planner = NotificationPlanner(
                    notifications, recipient=runtime_settings.smtp_recipient
                )
                worker = AuctionWatchWorker(
                    RunWorker(
                        application.state.run_engine,
                        application.state.profile_repository,
                        application.state.operational_repository,
                        queue,
                        planner,
                    ),
                    NotificationDeliveryWorker(notifications, sender),
                )
                application.state.run_queue = queue
                application.state.notifications = notifications
                application.state.worker = worker
                if runtime_settings.worker_enabled:
                    worker_thread = Thread(
                        target=worker.run_forever,
                        args=(stop_worker,),
                        kwargs={"poll_seconds": runtime_settings.worker_poll_seconds},
                        name="auction-watch-worker",
                        daemon=True,
                    )
                    worker_thread.start()
            yield
        finally:
            stop_worker.set()
            if worker_thread is not None:
                worker_thread.join(timeout=5)
            if database is not None:
                database.dispose()
            application.state.database = None
            application.state.profile_repository = None
            application.state.operational_repository = None
            application.state.run_engine = None
            application.state.run_queue = None
            application.state.notifications = None
            application.state.worker = None

    application = FastAPI(title="Auction Watch", version=__version__, lifespan=lifespan)
    application.include_router(profiles_router)
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
