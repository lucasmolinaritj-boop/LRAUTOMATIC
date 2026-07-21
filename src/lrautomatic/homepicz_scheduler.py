from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .catalogs import create_catalog
from .config import Settings, load_settings
from .models import ImportJob, ImportJobRequest, ImportSource
from .store import JobStore

log = logging.getLogger("lrautomatic.homepicz")
CONFIG_POLL_SECONDS = 1.0
HOME_PICZ_COLLECTION_PREFIX = "Home Picz - "


@dataclass(frozen=True, slots=True)
class ImportWindow:
    start: date
    end: date

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%d-%m-%Y")
        return f"{self.start:%d-%m-%Y}_a_{self.end:%d-%m-%Y}"


def operational_today(settings: Settings, now: datetime | None = None) -> date:
    current = now or datetime.now()
    result = current.date()
    if current.time().replace(second=0, microsecond=0) >= settings.day_rollover_time:
        result += timedelta(days=1)
    return result


def previous_business_window(today: date | None = None) -> ImportWindow:
    today = today or date.today()
    weekday = today.weekday()
    if weekday == 5:
        return ImportWindow(today - timedelta(days=1), today)
    if weekday == 6:
        return ImportWindow(today - timedelta(days=2), today - timedelta(days=1))
    if weekday == 0:
        return ImportWindow(today - timedelta(days=3), today - timedelta(days=2))
    yesterday = today - timedelta(days=1)
    return ImportWindow(yesterday, yesterday)


def current_import_window(settings: Settings, now: datetime | None = None) -> ImportWindow:
    return previous_business_window(operational_today(settings, now))


def _fetch_ids(settings: Settings, window: ImportWindow) -> list[str]:
    if not settings.homepicz_appscript_url:
        raise RuntimeError("Configure homepicz_appscript_url")
    if window.start == window.end:
        query = urllib.parse.urlencode({"data": window.start.isoformat()})
    else:
        query = urllib.parse.urlencode({"inicio": window.start.isoformat(), "fim": window.end.isoformat()})
    with urllib.request.urlopen(f"{settings.homepicz_appscript_url}?{query}", timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list):
        raise RuntimeError("Apps Script respondeu sem o campo ids")
    return list(dict.fromkeys(str(value).strip() for value in ids if str(value).strip()))


def _catalog_for_window(settings: Settings, window: ImportWindow) -> Path:
    name = f"Home Picz - {window.label}"
    expected = settings.catalog_output_root / name / f"{name}.lrcat" if settings.catalog_output_root else None
    if expected and expected.is_file():
        catalog_path = expected
    else:
        catalog_path = create_catalog(settings, name, open_lightroom=False).catalog_path
    settings.desired_catalog_file.write_text(str(catalog_path), encoding="utf-8")
    return catalog_path


def _write_state(settings: Settings, payload: dict[str, object]) -> None:
    payload = dict(payload)
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    settings.scheduler_state_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_homepicz_job(job: ImportJob) -> bool:
    return bool(job.request.collection_set or "").startswith(HOME_PICZ_COLLECTION_PREFIX)


def _normalize_path(value: str) -> str:
    return str(Path(value).expanduser().resolve()).casefold()


def _count_source_files(folder: Path, recursive: bool, allowed_extensions: set[str]) -> int:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    count = 0
    try:
        for path in iterator:
            try:
                if not path.is_file():
                    continue
                if path.suffix.lower().lstrip(".") not in allowed_extensions:
                    continue
                if path.stat().st_size <= 0:
                    continue
                count += 1
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        return 0
    return count


def _covered_counts(jobs: list[ImportJob], collection_set: str) -> dict[str, int]:
    covered: dict[str, int] = {}
    for job in jobs:
        if not _is_homepicz_job(job) or job.request.collection_set != collection_set:
            continue
        status = str(job.status)
        active = status in {"queued", "running"}
        terminal = status in {"completed", "partial"}
        if not active and not terminal:
            continue

        progress_by_path = {_normalize_path(item.path): item for item in job.progress}
        for source in job.request.sources:
            key = _normalize_path(source.path)
            progress = progress_by_path.get(key)
            source_completed = active or (
                terminal and progress is not None and str(progress.status) == "completed"
            )
            if not source_completed:
                continue
            known = max(
                int(source.expected_count or 0),
                int(progress.discovered if progress is not None else 0),
            )
            covered[key] = max(covered.get(key, 0), known)
    return covered


def _cancel_obsolete_queued_jobs(
    store: JobStore,
    jobs: list[ImportJob],
    current_collection_set: str,
) -> list[str]:
    cancelled: list[str] = []
    for job in jobs:
        if str(job.status) != "queued" or not _is_homepicz_job(job):
            continue
        if job.request.collection_set == current_collection_set:
            continue
        job.status = "cancelled"
        job.finished_at = job.finished_at or job.updated_at
        job.add_event(
            "superseded_period",
            "Tarefa descartada após mudança do período operacional",
            f"Período antigo: {job.request.collection_set}; período atual: {current_collection_set}.",
            level="warning",
        )
        store.save(job)
        cancelled.append(job.job_id)
    return cancelled


def run_cycle(settings: Settings, store: JobStore, now: datetime | None = None) -> dict[str, object]:
    current = now or datetime.now()
    effective_today = operational_today(settings, current)
    window = previous_business_window(effective_today)
    collection_set = f"Home Picz - {window.label}"
    jobs = store.list()

    base_result: dict[str, object] = {
        "at": current.isoformat(timespec="seconds"),
        "calendar_date": current.date().isoformat(),
        "operational_today": effective_today.isoformat(),
        "day_rollover_time": settings.homepicz_day_rollover_time,
        "rollover_applied": effective_today != current.date(),
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "collection_set": collection_set,
    }

    running_jobs = [job for job in jobs if str(job.status) == "running"]
    running_other_period = [
        job for job in running_jobs if job.request.collection_set != collection_set
    ]
    if running_other_period:
        result = {
            **base_result,
            "status": "deferred_catalog_switch",
            "running_job_ids": [job.job_id for job in running_other_period],
            "reason": "Há um job de outro período em execução; a troca de catálogo foi adiada.",
        }
        _write_state(settings, result)
        return result

    cancelled_job_ids = _cancel_obsolete_queued_jobs(store, jobs, collection_set)
    catalog_path = _catalog_for_window(settings, window)
    ids = _fetch_ids(settings, window)

    allowed = {value.lower().lstrip(".") for value in settings.allowed_extensions}
    recursive = bool(settings.homepicz_recursive)
    covered = _covered_counts(jobs, collection_set)

    sources: list[ImportSource] = []
    missing: list[str] = []
    unchanged: list[str] = []
    empty: list[str] = []

    for work_id in ids:
        folder = settings.homepicz_photos_root / work_id
        if not folder.is_dir():
            missing.append(work_id)
            continue

        expected_count = _count_source_files(folder, recursive, allowed)
        if expected_count <= 0:
            empty.append(work_id)
            continue

        key = _normalize_path(str(folder))
        if expected_count <= covered.get(key, -1):
            unchanged.append(work_id)
            continue

        sources.append(
            ImportSource(
                path=str(folder),
                collection=work_id,
                expected_count=expected_count,
            )
        )

    total_photos = sum(source.expected_count for source in sources)
    result: dict[str, object] = {
        **base_result,
        "status": "up_to_date",
        "catalog_path": str(catalog_path),
        "ids": len(ids),
        "new_or_expanded_sources": len(sources),
        "known_unchanged_sources": len(unchanged),
        "expected_photos": total_photos,
        "missing_ids": missing,
        "empty_ids": empty,
        "allowed_extensions": settings.allowed_extensions,
        "standard_previews": settings.homepicz_standard_previews,
        "standard_preview_size": settings.homepicz_standard_preview_size,
        "smart_previews": settings.homepicz_smart_previews,
        "cancelled_obsolete_job_ids": cancelled_job_ids,
    }

    if sources:
        request = ImportJobRequest(
            sources=sources,
            collection_set=collection_set,
            recursive=recursive,
            build_standard_previews=settings.homepicz_standard_previews,
            standard_preview_size=settings.homepicz_standard_preview_size,
            build_smart_previews=settings.homepicz_smart_previews,
            allowed_extensions=settings.allowed_extensions,
            develop_preset_name=settings.homepicz_preset_name,
            duplicate_policy="skip",
        )
        job = store.create(request)
        result["job_id"] = job.job_id
        result["status"] = "job_created"
        result["queue_mode"] = "incremental"
        result["running_job_count"] = len(running_jobs)
        log.info(
            "Job incremental criado: job=%s período=%s fontes=%s fotos=%s em_execução=%s",
            job.job_id,
            collection_set,
            len(sources),
            total_photos,
            len(running_jobs),
        )
    else:
        log.info(
            "Nenhum trabalho novo: período=%s ids=%s inalterados=%s vazios=%s ausentes=%s",
            collection_set,
            len(ids),
            len(unchanged),
            len(empty),
            len(missing),
        )

    _write_state(settings, result)
    return result


class HomePiczScheduler:
    def __init__(self, settings: Settings, store: JobStore, config_path: str | Path | None = None):
        self.settings = settings
        self.store = store
        self.config_path = Path(config_path).expanduser().resolve() if config_path else None
        self.config_mtime_ns = self._config_mtime_ns()
        self.stop_event = threading.Event()
        self.first_cycle_done = threading.Event()
        self.thread: threading.Thread | None = None

    def _config_mtime_ns(self) -> int | None:
        if self.config_path is None:
            return None
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def _reload_settings_if_changed(self) -> bool:
        if self.config_path is None:
            return False
        current_mtime = self._config_mtime_ns()
        if current_mtime is None or current_mtime == self.config_mtime_ns:
            return False
        try:
            new_settings = load_settings(self.config_path)
            new_store = JobStore(new_settings)
        except Exception:
            log.exception("Configuração alterada, mas não pôde ser recarregada; mantendo valores anteriores")
            return False
        old_interval = self.settings.homepicz_interval_minutes
        self.settings = new_settings
        self.store = new_store
        self.config_mtime_ns = current_mtime
        log.info(
            "Configuração recarregada automaticamente; intervalo %s -> %s minuto(s)",
            old_interval,
            new_settings.homepicz_interval_minutes,
        )
        return True

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.first_cycle_done.clear()
        self.thread = threading.Thread(target=self._loop, name="HomePiczScheduler", daemon=True)
        self.thread.start()
        log.info(
            "Scheduler Home Picz iniciado; fila incremental; virada operacional às %s",
            self.settings.homepicz_day_rollover_time,
        )

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _wait_until_next_cycle(self, cycle_finished_at: float) -> None:
        while not self.stop_event.is_set():
            self._reload_settings_if_changed()
            interval_seconds = max(60, self.settings.homepicz_interval_minutes * 60)
            elapsed = time.monotonic() - cycle_finished_at
            remaining = interval_seconds - elapsed
            if remaining <= 0:
                return
            self.stop_event.wait(min(CONFIG_POLL_SECONDS, remaining))

    def _loop(self) -> None:
        first_cycle = True
        while not self.stop_event.is_set():
            self._reload_settings_if_changed()
            started_at = datetime.now().isoformat(timespec="seconds")
            try:
                log.info("Iniciando ciclo Home Picz%s", " imediato" if first_cycle else "")
                result = run_cycle(self.settings, self.store)
                log.info("Ciclo Home Picz concluído: %s", result)
            except Exception as exc:
                log.exception("Falha no ciclo automático Home Picz")
                _write_state(
                    self.settings,
                    {
                        "status": "failed",
                        "at": started_at,
                        "day_rollover_time": self.settings.homepicz_day_rollover_time,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            finally:
                if first_cycle:
                    self.first_cycle_done.set()
                    first_cycle = False

            cycle_finished_at = time.monotonic()
            interval_seconds = max(60, self.settings.homepicz_interval_minutes * 60)
            next_run = datetime.now() + timedelta(seconds=interval_seconds)
            log.info("Próxima verificação Home Picz em %s", next_run.isoformat(timespec="seconds"))
            self._wait_until_next_cycle(cycle_finished_at)
