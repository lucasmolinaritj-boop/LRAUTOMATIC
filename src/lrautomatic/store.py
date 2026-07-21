from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .config import Settings
from .models import ImportJob, ImportJobRequest, SourceProgress


class JobStore:
    READ_RETRIES = 2
    READ_RETRY_DELAY_SECONDS = 0.03
    MISSING_GRACE_REFRESHES = 5

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_dirs()
        self._last_good_jobs: dict[str, ImportJob] = {}
        self._missing_refreshes: dict[str, int] = {}
        self._file_signatures: dict[str, tuple[int, int]] = {}

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

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def create(self, request: ImportJobRequest) -> ImportJob:
        # A descoberta definitiva pertence ao executor, no instante em que o job
        # começa. Isso evita travar a interface e impede contagens desatualizadas.
        progress = [
            SourceProgress(
                path=source.path,
                collection=source.collection or Path(source.path).name,
                discovered=0,
            )
            for source in request.sources
        ]
        job = ImportJob(request=request, progress=progress, total_discovered=0)
        if request.build_standard_previews:
            job.standard_previews_status = "requested"
        if request.build_smart_previews:
            job.smart_previews_status = "requested"
        if request.develop_preset_name or request.develop_preset_uuid:
            job.preset_status = "requested"

        job.add_event(
            "queue",
            "Tarefa criada",
            f"{len(request.sources)} pasta(s) adicionada(s) à fila; arquivos serão descobertos quando o job iniciar.",
        )
        self.save(job)
        return job

    def save(self, job: ImportJob) -> None:
        job.touch()
        path = self._job_path(job.job_id)
        self._atomic_write(path, job.model_dump(mode="json"))
        self._last_good_jobs[job.job_id] = job
        signature = self._signature(path)
        if signature is not None:
            self._file_signatures[job.job_id] = signature
        self._missing_refreshes.pop(job.job_id, None)

    def _read_job_with_retry(self, path: Path) -> ImportJob | None:
        for attempt in range(self.READ_RETRIES):
            try:
                return ImportJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                if attempt + 1 < self.READ_RETRIES:
                    time.sleep(self.READ_RETRY_DELAY_SECONDS)
            except Exception:
                if attempt + 1 < self.READ_RETRIES:
                    time.sleep(self.READ_RETRY_DELAY_SECONDS)
        return None

    def get(self, job_id: str) -> ImportJob:
        path = self._job_path(job_id)
        signature = self._signature(path)
        cached = self._last_good_jobs.get(job_id)
        if cached is not None and signature is not None and self._file_signatures.get(job_id) == signature:
            return cached
        job = self._read_job_with_retry(path)
        if job is not None:
            self._last_good_jobs[job_id] = job
            if signature is not None:
                self._file_signatures[job_id] = signature
            self._missing_refreshes.pop(job_id, None)
            return job
        if cached is not None:
            return cached
        raise FileNotFoundError(job_id)

    def list(self) -> list[ImportJob]:
        current: dict[str, ImportJob] = {}
        try:
            paths = list(self.settings.jobs_dir.glob("job_*.json"))
        except OSError:
            paths = []

        for path in paths:
            job_id = path.stem
            signature = self._signature(path)
            cached = self._last_good_jobs.get(job_id)
            if cached is not None and signature is not None and self._file_signatures.get(job_id) == signature:
                job = cached
            else:
                job = self._read_job_with_retry(path)
                if job is None:
                    job = cached
            if job is not None:
                current[job.job_id] = job
                self._last_good_jobs[job.job_id] = job
                if signature is not None:
                    self._file_signatures[job.job_id] = signature
                self._missing_refreshes.pop(job.job_id, None)

        for job_id, cached in list(self._last_good_jobs.items()):
            if job_id in current:
                continue
            misses = self._missing_refreshes.get(job_id, 0) + 1
            self._missing_refreshes[job_id] = misses
            if misses <= self.MISSING_GRACE_REFRESHES:
                current[job_id] = cached
            else:
                self._last_good_jobs.pop(job_id, None)
                self._file_signatures.pop(job_id, None)
                self._missing_refreshes.pop(job_id, None)

        return sorted(current.values(), key=lambda job: job.created_at, reverse=True)

    def cancel(self, job_id: str) -> ImportJob:
        job = self.get(job_id)
        if job.status not in {"completed", "failed", "cancelled"}:
            job.status = "cancelled"
            job.finished_at = job.finished_at or job.updated_at
            job.add_event("cancelled", "Tarefa cancelada pelo usuário", level="warning")
            self.save(job)
        return job