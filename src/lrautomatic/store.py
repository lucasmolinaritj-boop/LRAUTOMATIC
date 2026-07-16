from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import Settings
from .models import ImportJob, ImportJobRequest, SourceProgress


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_dirs()

    def _job_path(self, job_id: str) -> Path:
        return self.settings.jobs_dir / f"{job_id}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def create(self, request: ImportJobRequest) -> ImportJob:
        progress = [
            SourceProgress(path=source.path, collection=source.collection or Path(source.path).name)
            for source in request.sources
        ]
        job = ImportJob(request=request, progress=progress)
        if request.build_smart_previews:
            job.smart_previews_status = "requested_but_sdk_not_supported"
        self.save(job)
        return job

    def save(self, job: ImportJob) -> None:
        job.touch()
        self._atomic_write(self._job_path(job.job_id), job.model_dump(mode="json"))

    def get(self, job_id: str) -> ImportJob:
        path = self._job_path(job_id)
        if not path.exists():
            raise FileNotFoundError(job_id)
        return ImportJob.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[ImportJob]:
        jobs: list[ImportJob] = []
        for path in sorted(self.settings.jobs_dir.glob("job_*.json"), reverse=True):
            try:
                jobs.append(ImportJob.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return jobs

    def cancel(self, job_id: str) -> ImportJob:
        job = self.get(job_id)
        if job.status not in {"completed", "failed", "cancelled"}:
            job.status = "cancelled"
            self.save(job)
        return job
