from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import ImportJob
from .store import JobStore

TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "interrupted"}
ACTIVE_STATUSES = {"queued", "running"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strict_jobs(store: JobStore) -> tuple[list[ImportJob], list[str]]:
    recovered = store.recover_stale_running_jobs()
    return store.list_strict(), recovered


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


def preflight(store: JobStore, prefix: str = "Home Picz - ") -> tuple[list[ImportJob], list[str], list[ImportJob]]:
    jobs, recovered = strict_jobs(store)
    active = active_homepicz_jobs(jobs, prefix)
    return jobs, recovered, active
