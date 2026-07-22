from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .models import ImportJob, ImportJobRequest, SourceProgress, utc_now


class JobStore:
    READ_RETRIES = 3
    READ_RETRY_DELAY_SECONDS = 0.05
    MISSING_GRACE_REFRESHES = 12
    HOME_PICZ_COLLECTION_PREFIX = "Home Picz - "
    ACTIVE_STATUSES = {"queued", "running"}
    STALE_RUNNING_SECONDS = 15 * 60
    CREATE_LOCK_STALE_SECONDS = 60

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_dirs()
        self._last_good_jobs: dict[str, ImportJob] = {}
        self._missing_refreshes: dict[str, int] = {}
        self._file_signatures: dict[str, tuple[int, int]] = {}
        self._lock = threading.RLock()

    def _job_path(self, job_id: str) -> Path:
        return self.settings.jobs_dir / f"{job_id}.json"

    @property
    def _create_lock_path(self) -> Path:
        return self.settings.jobs_dir / ".homepicz-create.lock"

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
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    @classmethod
    def _is_homepicz_request(cls, request: ImportJobRequest) -> bool:
        return str(request.collection_set or "").startswith(cls.HOME_PICZ_COLLECTION_PREFIX)

    @classmethod
    def _is_active_homepicz_job(cls, job: ImportJob) -> bool:
        return (
            str(job.request.collection_set or "").startswith(cls.HOME_PICZ_COLLECTION_PREFIX)
            and str(job.status) in cls.ACTIVE_STATUSES
        )

    @staticmethod
    def _parse_iso_epoch(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None

    def _heartbeat_epoch(self, job: ImportJob) -> float | None:
        if job.runner_heartbeat_epoch:
            return float(job.runner_heartbeat_epoch)
        return self._parse_iso_epoch(job.runner_heartbeat_at) or self._parse_iso_epoch(job.updated_at)

    def recover_stale_running_jobs(self, now_epoch: float | None = None) -> list[str]:
        now_epoch = now_epoch or time.time()
        recovered: list[str] = []
        for job in self.list_strict():
            if str(job.status) != "running":
                continue
            heartbeat = self._heartbeat_epoch(job)
            if heartbeat is None or now_epoch - heartbeat < self.STALE_RUNNING_SECONDS:
                continue
            job.status = "interrupted"
            job.interrupted_at = utc_now()
            job.finished_at = utc_now()
            job.error = "Execução interrompida: heartbeat do Lightroom expirou."
            job.add_event(
                "interrupted",
                "Execução interrompida recuperada",
                "O Lightroom deixou de atualizar o heartbeat; o job foi liberado para não bloquear a fila.",
                level="warning",
            )
            self.save(job)
            recovered.append(job.job_id)
        return recovered

    @contextmanager
    def _process_create_lock(self):
        path = self._create_lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + 10
        fd: int | None = None
        while fd is None:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"pid={os.getpid()} created={time.time()}".encode("utf-8"))
            except FileExistsError:
                try:
                    age = time.time() - path.stat().st_mtime
                    if age > self.CREATE_LOCK_STALE_SECONDS:
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    raise RuntimeError("Não foi possível obter o lock de criação de jobs.")
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def create(self, request: ImportJobRequest) -> ImportJob:
        with self._lock:
            lock_context = self._process_create_lock() if self._is_homepicz_request(request) else contextmanager(lambda: (yield))()
            with lock_context:
                if self._is_homepicz_request(request):
                    self.recover_stale_running_jobs()
                    active_jobs = [job for job in self.list_strict() if self._is_active_homepicz_job(job)]
                    if active_jobs:
                        return min(active_jobs, key=lambda job: job.created_at)

                progress = [
                    SourceProgress(
                        path=source.path,
                        collection=source.collection or Path(source.path).name,
                        discovered=0,
                    )
                    for source in request.sources
                ]
                job = ImportJob(request=request, progress=progress, total_discovered=0)
                if request.organize_collections_by_photographer or request.organize_collections_by_client:
                    job.collections_status = "requested"
                    job.collections_run_once_token = job.job_id
                if request.build_standard_previews:
                    job.standard_previews_status = "requested"
                if request.build_smart_previews:
                    job.smart_previews_status = "requested"
                if request.develop_preset_name or request.develop_preset_uuid:
                    job.preset_status = "requested"
                job.add_event(
                    "queue",
                    "Tarefa criada",
                    f"{len(request.sources)} pasta(s) adicionada(s) à fila; o Lightroom confirmará o total ao iniciar.",
                )
                self.save(job)
                return job

    def save(self, job: ImportJob) -> None:
        with self._lock:
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
                text = path.read_text(encoding="utf-8")
                if not text.strip():
                    raise ValueError("JSON vazio")
                return ImportJob.model_validate_json(text)
            except (FileNotFoundError, PermissionError, OSError, ValueError, TypeError):
                if attempt + 1 < self.READ_RETRIES:
                    time.sleep(self.READ_RETRY_DELAY_SECONDS * (attempt + 1))
            except Exception:
                if attempt + 1 < self.READ_RETRIES:
                    time.sleep(self.READ_RETRY_DELAY_SECONDS * (attempt + 1))
        return None

    def get(self, job_id: str) -> ImportJob:
        with self._lock:
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

    def list_strict(self) -> list[ImportJob]:
        with self._lock:
            try:
                paths = list(self.settings.jobs_dir.glob("job_*.json"))
            except OSError:
                return []
            jobs: list[ImportJob] = []
            for path in paths:
                job = self._read_job_with_retry(path)
                if job is not None:
                    jobs.append(job)
            return sorted(jobs, key=lambda job: job.created_at, reverse=True)

    def list(self) -> list[ImportJob]:
        with self._lock:
            current: dict[str, ImportJob] = {}
            try:
                paths = list(self.settings.jobs_dir.glob("job_*.json"))
                listing_ok = True
            except OSError:
                paths = []
                listing_ok = False

            if not listing_ok:
                return sorted(self._last_good_jobs.values(), key=lambda job: job.created_at, reverse=True)

            for path in paths:
                job_id = path.stem
                signature = self._signature(path)
                cached = self._last_good_jobs.get(job_id)
                if cached is not None and signature is not None and self._file_signatures.get(job_id) == signature:
                    job = cached
                else:
                    job = self._read_job_with_retry(path) or cached
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
        with self._lock:
            job = self.get(job_id)
            if str(job.status) not in {"completed", "failed", "cancelled", "interrupted"}:
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.add_event("cancelled", "Tarefa cancelada pelo usuário", level="warning")
                self.save(job)
            return job
