from __future__ import annotations

from fastapi import FastAPI

from .api import files
from .config import Settings
from .db import Repository


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="Geo Measure API",
        version="1.0.0",
        description="Upload a zipped Shapefile or KML and get per-feature area/length.",
    )
    app.state.settings = settings
    app.state.repo = Repository(settings.db_path)
    app.include_router(files.router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
