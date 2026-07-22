from __future__ import annotations

from typing import Any, Callable

from .homepicz_queue_policy import active_homepicz_jobs, preflight
from .store import JobStore


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
    result = original_run_cycle(settings, store, now)
    result.setdefault("recovered_stale_job_ids", recovered)
    return result
