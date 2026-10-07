"""FastAPI application factory and entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import files
from app.config import get_settings
from app.database import Base, engine
from app.exceptions import ApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///./"):
        # make sure the folder for a relative SQLite file exists
        from pathlib import Path

        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )
    Base.metadata.create_all(engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Geospatial File Measurement API",
        description=(
            "Upload a zipped Shapefile or a KML file; get back its features and "
            "projection-correct area / length measurements."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        body: dict = {"detail": exc.message}
        if exc.file_id:
            body["file_id"] = exc.file_id
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(files.router)
    return app


app = create_app()
