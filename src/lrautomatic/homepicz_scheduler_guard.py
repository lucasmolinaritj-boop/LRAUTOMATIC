from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .homepicz_queue_policy import preflight
from .store import JobStore

HOME_PICZ_PREFIX = "Home Picz - "
TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "interrupted"}


def _parse_finished_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _last_finished_homepicz_job(jobs: list[Any]) -> tuple[Any | None, datetime | None]:
    latest_job = None
    latest_finished = None
    for job in jobs:
        collection_set = str(job.request.collection_set or "")
        if not collection_set.startswith(HOME_PICZ_PREFIX):
            continue
        if str(job.status) not in TERMINAL_STATUSES:
            continue
        finished = _parse_finished_at(job.finished_at)
        if finished is None:
            continue
        if latest_finished is None or finished > latest_finished:
            latest_job = job
            latest_finished = finished
    return latest_job, latest_finished


def guarded_cycle(
    store: JobStore,
    original_run_cycle: Callable[..., dict[str, object]],
    settings: Any,
    now: Any = None,
) -> dict[str, object]:
    jobs, recovered, active = preflight(store)
    if active:
        return {
            "status": "deferred_active_job",
            "active_job_ids": [job.job_id for job in active],
            "active_job_statuses": [str(job.status) for job in active],
            "recovered_stale_job_ids": recovered,
            "reason": "Já existe um job Home Picz queued/running; novo job só será criado após ele terminar.",
        }

    interval_minutes = max(1, int(settings.homepicz_interval_minutes or 1))
    last_job, last_finished = _last_finished_homepicz_job(jobs)
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    if last_finished is not None:
        next_allowed_at = last_finished + timedelta(minutes=interval_minutes)
        if current < next_allowed_at:
            remaining_seconds = max(1, int((next_allowed_at - current).total_seconds()))
            return {
                "status": "deferred_execution_interval",
                "last_job_id": last_job.job_id if last_job is not None else None,
                "last_job_finished_at": last_finished.isoformat(),
                "next_execution_allowed_at": next_allowed_at.isoformat(),
                "interval_minutes": interval_minutes,
                "remaining_seconds": remaining_seconds,
                "recovered_stale_job_ids": recovered,
                "reason": "O intervalo configurado é contado a partir do término do último job Home Picz.",
            }

    result = original_run_cycle(settings, store, now)
    result.setdefault("recovered_stale_job_ids", recovered)
    result.setdefault("execution_interval_minutes", interval_minutes)
    return result
