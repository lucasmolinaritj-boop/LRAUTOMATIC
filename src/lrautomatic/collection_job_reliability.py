from __future__ import annotations

from typing import Any

COLLECTION_ORGANIZATION_VERSION = 4
_PENDING_COLLECTION_STATUSES = {"requested", "running"}


def _collection_enabled(job: Any) -> bool:
    request = getattr(job, "request", None)
    return bool(
        getattr(request, "organize_collections_by_photographer", False)
        or getattr(request, "organize_collections_by_client", False)
    )


def _collection_pending(job: Any) -> bool:
    return _collection_enabled(job) and str(
        getattr(job, "collections_status", "") or ""
    ) in _PENDING_COLLECTION_STATUSES


def install_collection_job_reliability() -> None:
    """Garante que jobs terminados só sejam arquivados após organizar coleções."""
    try:
        from . import homepicz_scheduler as scheduler
        from .store import JobStore
    except Exception:
        return

    scheduler.COLLECTION_ORGANIZATION_VERSION = COLLECTION_ORGANIZATION_VERSION

    if not getattr(JobStore, "_collection_archive_guard_installed", False):
        original_save = JobStore.save

        def guarded_archive_terminal_jobs(self) -> None:
            try:
                paths = list(self.settings.jobs_dir.glob("job_*.json"))
            except OSError:
                return

            for path in paths:
                job = self._read_job_with_retry(path)
                if job is None or str(job.status) not in self.TERMINAL_STATUSES:
                    continue
                # O plugin do Lightroom só lê a pasta jobs. Arquivar antes desta
                # etapa fazia a reorganização desaparecer de forma intermitente.
                if _collection_pending(job):
                    continue
                try:
                    self._archive_job_file(path, job)
                except OSError:
                    continue

        def guarded_save(self, job) -> None:
            if str(job.status) not in self.TERMINAL_STATUSES or not _collection_pending(job):
                original_save(self, job)
                return

            # Mantém jobs terminais com coleções pendentes na fila ativa até o
            # CollectionOrganizer confirmar completed/partial.
            with self._lock:
                job.touch()
                self._compact_events(job)
                path = self._job_path(job.job_id)
                self._atomic_write(path, job.model_dump(mode="json"))
                self._last_good_jobs[job.job_id] = job
                signature = self._signature(path)
                if signature is not None:
                    self._file_signatures[job.job_id] = signature
                self._missing_refreshes.pop(job.job_id, None)
                self._history_last_read = 0.0

        JobStore._archive_terminal_jobs = guarded_archive_terminal_jobs
        JobStore.save = guarded_save
        JobStore._collection_archive_guard_installed = True

    original_refresh = getattr(scheduler, "_refresh_existing_job_metadata", None)
    if original_refresh is None or getattr(original_refresh, "_collection_reliability_wrapped", False):
        return

    def reliable_refresh(store, jobs, collection_set, items):
        updated_ids = list(original_refresh(store, jobs, collection_set, items))
        updated = set(updated_ids)

        for job in jobs:
            request = getattr(job, "request", None)
            if request is None or str(getattr(request, "collection_set", "") or "") != collection_set:
                continue
            if not _collection_enabled(job):
                continue

            request_version = int(
                getattr(request, "collection_organization_version", 0) or 0
            )
            applied_version = int(
                getattr(job, "collections_organization_version", 0) or 0
            )
            status = str(getattr(job, "collections_status", "") or "")
            active_path_exists = store._job_path(job.job_id).exists()

            # "running" dentro de jobs/ significa que o Lightroom está trabalhando
            # agora. Só recuperamos esse estado quando ele apareceu arquivado, o que
            # indica interrupção antiga e não uma operação concorrente legítima.
            actively_organizing = status == "running" and active_path_exists
            if actively_organizing:
                continue

            needs_reconcile = (
                request_version < COLLECTION_ORGANIZATION_VERSION
                or (
                    applied_version < COLLECTION_ORGANIZATION_VERSION
                    and status not in {"partial", "failed"}
                )
                or status == "requested"
                or (status == "running" and not active_path_exists)
            )
            if not needs_reconcile:
                continue

            request.collection_organization_version = COLLECTION_ORGANIZATION_VERSION
            job.collections_status = "requested"
            job.collections_organization_version = 0
            job.collections_run_once_token = job.job_id

            if job.job_id not in updated:
                job.add_event(
                    "collections_reconcile",
                    "Reorganização de coleções garantida",
                    "O job foi mantido na fila ativa até o Lightroom confirmar a organização das coleções.",
                )
                updated.add(job.job_id)
                updated_ids.append(job.job_id)

            # Se estava no histórico, guarded_save devolve o JSON para jobs/.
            store.save(job)

        return updated_ids

    reliable_refresh._collection_reliability_wrapped = True
    scheduler._refresh_existing_job_metadata = reliable_refresh
