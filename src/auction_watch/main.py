"""FastAPI entry point for the foundation service."""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from auction_watch import __version__
from auction_watch.config import Settings, get_settings

app = FastAPI(title="Auction Watch", version=__version__)


def _web_dist() -> Path:
    configured = os.environ.get("AW_WEB_DIST")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "web" / "dist"


if _web_dist().is_dir():
    app.mount("/assets", StaticFiles(directory=_web_dist() / "assets"), name="assets")


def _readiness(settings: Settings) -> tuple[bool, str | None]:
    data_dir = settings.data_dir
    if not data_dir.exists():
        return False, None
    if not data_dir.is_dir():
        return False, None
    if not os.access(data_dir, os.R_OK | os.W_OK | os.X_OK):
        return False, None
    return True, None


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    """Confirm that the process is alive without exposing runtime details."""

    return {"ok": True, "service": "auction-watch", "version": __version__}


@app.get("/api/v1/readiness")
def readiness() -> JSONResponse:
    """Confirm that the configured data directory is usable."""

    ready, _ = _readiness(get_settings())
    payload = {"ok": ready, "service": "auction-watch", "version": __version__}
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/", include_in_schema=False)
def index() -> Response:
    """Serve the compiled frontend when it is available."""

    index_file = _web_dist() / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return JSONResponse({"service": "auction-watch", "version": __version__})


def run() -> None:
    """Run the application with the configured host and port."""

    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
