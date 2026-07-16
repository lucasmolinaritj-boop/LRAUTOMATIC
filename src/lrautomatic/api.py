from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .config import Settings, load_settings
from .models import ImportJob, ImportJobRequest
from .store import JobStore


def create_app(config_path: str | Path = "config.json") -> FastAPI:
    settings = load_settings(config_path)
    store = JobStore(settings)
    app = FastAPI(title="LRAutomatic", version="0.1.0")

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.api_key}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chave inválida")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": "0.1.0",
            "data_dir": str(settings.data_dir),
            "plugin_queue": str(settings.jobs_dir),
        }

    @app.post("/api/v1/import-jobs", response_model=ImportJob, dependencies=[Depends(authorize)])
    def create_import_job(request: ImportJobRequest) -> ImportJob:
        return store.create(request)

    @app.get("/api/v1/import-jobs", response_model=list[ImportJob], dependencies=[Depends(authorize)])
    def list_import_jobs() -> list[ImportJob]:
        return store.list()

    @app.get("/api/v1/import-jobs/{job_id}", response_model=ImportJob, dependencies=[Depends(authorize)])
    def get_import_job(job_id: str) -> ImportJob:
        try:
            return store.get(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada") from exc

    @app.post("/api/v1/import-jobs/{job_id}/cancel", response_model=ImportJob, dependencies=[Depends(authorize)])
    def cancel_import_job(job_id: str) -> ImportJob:
        try:
            return store.cancel(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada") from exc

    app.state.settings = settings
    app.state.store = store
    return app


app = create_app()
