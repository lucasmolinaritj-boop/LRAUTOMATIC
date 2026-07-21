from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .config import Settings
from .models import ImportJob, ImportJobRequest, SourceProgress


class JobStore:
    READ_RETRIES = 3
    READ_RETRY_DELAY_SECONDS = 0.04
    MISSING_GRACE_REFRESHES = 5

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_dirs()
        # O Google Drive pode ocultar ou bloquear um JSON por alguns milissegundos
        # durante sincronização/substituição. Mantemos o último snapshot válido para
        # que a tarefa não suma visualmente e reapareça no próximo refresh.
        self._last_good_jobs: dict[str, ImportJob] = {}
        self._missing_refreshes: dict[str, int] = {}

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
    def _count_source_photos(path: Path, recursive: bool, allowed_extensions: set[str]) -> int:
        try:
            iterator = path.rglob("*") if recursive else path.iterdir()
            return sum(
                1
                for item in iterator
                if item.is_file() and item.suffix.lower().lstrip(".") in allowed_extensions
            )
        except OSError:
            # A contagem antecipada é informativa. O Lightroom fará a descoberta definitiva.
            return 0

    def create(self, request: ImportJobRequest) -> ImportJob:
        allowed_extensions = {extension.lower().lstrip(".") for extension in request.allowed_extensions}
        progress: list[SourceProgress] = []

        for source in request.sources:
            recursive = request.recursive if source.recursive is None else source.recursive
            discovered = self._count_source_photos(
                Path(source.path),
                recursive=bool(recursive),
                allowed_extensions=allowed_extensions,
            )
            progress.append(
                SourceProgress(
                    path=source.path,
                    collection=source.collection or Path(source.path).name,
                    discovered=discovered,
                )
            )

        total_discovered = sum(item.discovered for item in progress)
        job = ImportJob(
            request=request,
            progress=progress,
            total_discovered=total_discovered,
        )
        if request.build_standard_previews:
            job.standard_previews_status = "requested"
        if request.build_smart_previews:
            job.smart_previews_status = "requested"
        if request.develop_preset_name or request.develop_preset_uuid:
            job.preset_status = "requested"

        job.add_event(
            "queue",
            "Tarefa criada",
            f"{len(request.sources)} pasta(s) adicionada(s) à fila; {total_discovered} foto(s) encontrada(s) antecipadamente.",
        )
        self.save(job)
        return job

    def save(self, job: ImportJob) -> None:
        job.touch()
        self._atomic_write(self._job_path(job.job_id), job.model_dump(mode="json"))
        self._last_good_jobs[job.job_id] = job
        self._missing_refreshes.pop(job.job_id, None)

    def _read_job_with_retry(self, path: Path) -> ImportJob | None:
        for attempt in range(self.READ_RETRIES):
            try:
                return ImportJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                if attempt + 1 < self.READ_RETRIES:
                    time.sleep(self.READ_RETRY_DELAY_SECONDS)
            except Exception:
                # JSON temporariamente incompleto ou modelo ainda sendo substituído.
                if attempt + 1 < self.READ_RETRIES:
                    time.sleep(self.READ_RETRY_DELAY_SECONDS)
        return None

    def get(self, job_id: str) -> ImportJob:
        path = self._job_path(job_id)
        job = self._read_job_with_retry(path)
        if job is not None:
            self._last_good_jobs[job_id] = job
            self._missing_refreshes.pop(job_id, None)
            return job
        cached = self._last_good_jobs.get(job_id)
        if cached is not None:
            return cached
        raise FileNotFoundError(job_id)

    def list(self) -> list[ImportJob]:
        current: dict[str, ImportJob] = {}
        try:
            paths = list(self.settings.jobs_dir.glob("job_*.json"))
        except OSError:
            paths = []

        seen_ids: set[str] = set()
        for path in paths:
            job_id = path.stem
            seen_ids.add(job_id)
            job = self._read_job_with_retry(path)
            if job is None:
                job = self._last_good_jobs.get(job_id)
            if job is not None:
                current[job.job_id] = job
                self._last_good_jobs[job.job_id] = job
                self._missing_refreshes.pop(job.job_id, None)

        # Uma listagem transitória vazia/incompleta do Drive não deve apagar linhas
        # do monitor. O item só sai depois de faltar em vários refreshes consecutivos.
        for job_id, cached in list(self._last_good_jobs.items()):
            if job_id in current:
                continue
            misses = self._missing_refreshes.get(job_id, 0) + 1
            self._missing_refreshes[job_id] = misses
            if misses <= self.MISSING_GRACE_REFRESHES:
                current[job_id] = cached
            else:
                self._last_good_jobs.pop(job_id, None)
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
