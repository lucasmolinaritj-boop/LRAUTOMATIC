from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import ImportJob
from .store import JobStore

TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "interrupted"}
ACTIVE_STATUSES = {"queued", "running"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_epoch(value: object) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _recover_stale_queued_jobs(
    store: JobStore,
    jobs: list[ImportJob],
    prefix: str,
    stale_seconds: int,
) -> list[str]:
    now_epoch = datetime.now(timezone.utc).timestamp()
    recovered: list[str] = []
    for job in jobs:
        if str(job.status) != "queued":
            continue
        if not str(job.request.collection_set or "").startswith(prefix):
            continue
        reference = _parse_epoch(job.updated_at) or _parse_epoch(job.created_at)
        if reference is None or now_epoch - reference < stale_seconds:
            continue
        finished_at = utc_now()
        job.status = "interrupted"
        job.interrupted_at = finished_at
        job.finished_at = finished_at
        job.error = "Tarefa liberada: permaneceu na fila sem ser iniciada pelo Lightroom."
        job.add_event(
            "interrupted",
            "Tarefa queued expirada",
            "O Lightroom não iniciou o job dentro do limite; a fila foi liberada para permitir novas execuções.",
            level="warning",
        )
        store.save(job)
        recovered.append(job.job_id)
    return recovered


def strict_jobs(
    store: JobStore,
    prefix: str = "Home Picz - ",
    queued_stale_seconds: int = 3600,
) -> tuple[list[ImportJob], list[str]]:
    recovered = store.recover_stale_running_jobs()
    jobs = store.list_strict()
    recovered.extend(
        _recover_stale_queued_jobs(
            store,
            jobs,
            prefix,
            max(300, int(queued_stale_seconds)),
        )
    )
    if recovered:
        jobs = store.list_strict()
    return jobs, recovered


def active_homepicz_jobs(jobs: list[ImportJob], prefix: str = "Home Picz - ") -> list[ImportJob]:
    return [
        job
        for job in jobs
        if str(job.request.collection_set or "").startswith(prefix)
        and str(job.status) in ACTIVE_STATUSES
    ]


def can_refresh_metadata(job: ImportJob) -> bool:
    return str(job.status) in ACTIVE_STATUSES


def completed_coverage(jobs: list[ImportJob], collection_set: str, normalize_path) -> dict[str, int]:
    covered: dict[str, int] = {}
    for job in jobs:
        if job.request.collection_set != collection_set:
            continue
        if str(job.status) not in {"completed", "partial"}:
            continue
        progress_by_path = {normalize_path(item.path): item for item in job.progress}
        for source in job.request.sources:
            key = normalize_path(source.path)
            progress = progress_by_path.get(key)
            if progress is None or str(progress.status) != "completed":
                continue
            known = max(int(source.expected_count or 0), int(progress.discovered or 0))
            covered[key] = max(covered.get(key, 0), known)
    return covered


def mark_obsolete_cancelled(job: ImportJob) -> None:
    job.status = "cancelled"
    job.finished_at = utc_now()


def was_newly_created(job: ImportJob, request_sources: list[str]) -> bool:
    job_sources = [str(Path(source.path)) for source in job.request.sources]
    normalized_requested = [str(Path(path)) for path in request_sources]
    return job_sources == normalized_requested and str(job.status) == "queued"


def preflight(
    store: JobStore,
    prefix: str = "Home Picz - ",
    queued_stale_seconds: int = 3600,
) -> tuple[list[ImportJob], list[str], list[ImportJob]]:
    jobs, recovered = strict_jobs(store, prefix, queued_stale_seconds)
    active = active_homepicz_jobs(jobs, prefix)
    return jobs, recovered, active
