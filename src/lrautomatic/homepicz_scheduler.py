from __future__ import annotations

import json
import logging
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_id: str
    photographer: str = "Sem fotógrafo"
    service_name: str = "Serviço não informado"
    scheduled_at: str | None = None


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


def _normalized_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return "".join(char.lower() for char in text if char.isalnum())


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    return {_normalized_key(key): value for key, value in record.items()}


def _first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(_normalized_key(key))
        if value is not None and str(value).strip():
            return value
    return None


def _clean_name(value: object, fallback: str) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split()).strip(" -")
    return text or fallback


def _format_scheduled_at(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Sem data e hora"

    normalized = raw.replace("Z", "+00:00")
    for candidate in (normalized, normalized.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.strftime("%d-%m-%Y %Hh%M")
        except ValueError:
            pass

    formats = (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            if "%H" in fmt:
                return parsed.strftime("%d-%m-%Y %Hh%M")
            return parsed.strftime("%d-%m-%Y")
        except ValueError:
            continue
    return _clean_name(raw, "Sem data e hora")


def _collection_name(item: WorkItem) -> str:
    return " - ".join(
        (
            _clean_name(item.work_id, "Sem ID"),
            _format_scheduled_at(item.scheduled_at),
            _clean_name(item.service_name, "Serviço não informado"),
        )
    )


def _payload_records(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "trabalhos", "jobs", "records", "dados", "fotografias"):
        value = payload.get(key)
        if isinstance(value, list) and any(isinstance(item, dict) for item in value):
            return [item for item in value if isinstance(item, dict)]
    return []


def _parse_work_items(payload: object) -> list[WorkItem]:
    records = _payload_records(payload)
    items: list[WorkItem] = []

    for raw_record in records:
        record = _normalized_record(raw_record)
        work_id = _first_value(record, "id", "id trabalho", "id agenda", "codigo")
        if work_id is None:
            continue
        items.append(
            WorkItem(
                work_id=str(work_id).strip(),
                photographer=_clean_name(
                    _first_value(record, "fotografo", "fotógrafo", "photographer", "responsavel"),
                    "Sem fotógrafo",
                ),
                service_name=_clean_name(
                    _first_value(record, "servico", "serviço", "service", "tipo servico"),
                    "Serviço não informado",
                ),
                scheduled_at=str(
                    _first_value(
                        record,
                        "data hora",
                        "data/hora",
                        "data_hora",
                        "datetime",
                        "date time",
                        "horario",
                        "data",
                    )
                    or ""
                ).strip()
                or None,
            )
        )

    if not items and isinstance(payload, dict):
        ids = payload.get("ids")
        if isinstance(ids, list):
            items = [WorkItem(work_id=str(value).strip()) for value in ids if str(value).strip()]

    unique: dict[str, WorkItem] = {}
    for item in items:
        unique[item.work_id] = item
    return list(unique.values())


def _fetch_work_items(settings: Settings, window: ImportWindow) -> list[WorkItem]:
    if not settings.homepicz_appscript_url:
        raise RuntimeError("Configure homepicz_appscript_url")
    params = (
        {"data": window.start.isoformat(), "detalhes": "1"}
        if window.start == window.end
        else {"inicio": window.start.isoformat(), "fim": window.end.isoformat(), "detalhes": "1"}
    )
    separator = "&" if "?" in settings.homepicz_appscript_url else "?"
    url = f"{settings.homepicz_appscript_url}{separator}{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    items = _parse_work_items(payload)
    if not items:
        raise RuntimeError("Apps Script respondeu sem trabalhos válidos")
    return items


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
    data = dict(payload)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    settings.scheduler_state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
                if not path.is_file() or path.suffix.lower().lstrip(".") not in allowed_extensions:
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
        if status not in {"queued", "running", "completed", "partial"}:
            continue
        progress_by_path = {_normalize_path(item.path): item for item in job.progress}
        for source in job.request.sources:
            key = _normalize_path(source.path)
            progress = progress_by_path.get(key)
            source_completed = status in {"queued", "running"} or (
                status in {"completed", "partial"}
                and progress is not None
                and str(progress.status) == "completed"
            )
            if not source_completed:
                continue
            known = max(int(source.expected_count or 0), int(progress.discovered if progress else 0))
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
    running_other_period = [job for job in running_jobs if job.request.collection_set != collection_set]
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
    items = _fetch_work_items(settings, window)

    allowed = {value.lower().lstrip(".") for value in settings.allowed_extensions}
    recursive = bool(settings.homepicz_recursive)
    covered = _covered_counts(jobs, collection_set)

    sources: list[ImportSource] = []
    missing: list[str] = []
    unchanged: list[str] = []
    empty: list[str] = []

    for item in items:
        folder = settings.homepicz_photos_root / item.work_id
        if not folder.is_dir():
            missing.append(item.work_id)
            continue
        expected_count = _count_source_files(folder, recursive, allowed)
        if expected_count <= 0:
            empty.append(item.work_id)
            continue
        key = _normalize_path(str(folder))
        if expected_count <= covered.get(key, -1):
            unchanged.append(item.work_id)
            continue
        sources.append(
            ImportSource(
                path=str(folder),
                collection=_collection_name(item),
                expected_count=expected_count,
                work_id=item.work_id,
                photographer=_clean_name(item.photographer, "Sem fotógrafo"),
                service_name=_clean_name(item.service_name, "Serviço não informado"),
                scheduled_at=item.scheduled_at,
            )
        )

    total_photos = sum(source.expected_count for source in sources)
    result: dict[str, object] = {
        **base_result,
        "status": "up_to_date",
        "catalog_path": str(catalog_path),
        "ids": len(items),
        "new_or_expanded_sources": len(sources),
        "known_unchanged_sources": len(unchanged),
        "expected_photos": total_photos,
        "missing_ids": missing,
        "empty_ids": empty,
        "collection_structure": "Fotógrafo > ID - data e hora - serviço",
        "cancelled_obsolete_job_ids": cancelled_job_ids,
    }

    if sources:
        request = ImportJobRequest(
            sources=sources,
            collection_set=collection_set,
            recursive=recursive,
            create_collections=False,
            organize_collections_by_photographer=True,
            build_standard_previews=settings.homepicz_standard_previews,
            standard_preview_size=settings.homepicz_standard_preview_size,
            build_smart_previews=settings.homepicz_smart_previews,
            allowed_extensions=settings.allowed_extensions,
            develop_preset_name=settings.homepicz_preset_name,
            duplicate_policy="skip",
        )
        job = store.create(request)
        result.update(
            job_id=job.job_id,
            status="job_created",
            queue_mode="incremental",
            running_job_count=len(running_jobs),
        )
        log.info(
            "Job incremental organizado criado: job=%s fontes=%s fotos=%s fotógrafos=%s",
            job.job_id,
            len(sources),
            total_photos,
            len({source.photographer for source in sources}),
        )
    else:
        log.info(
            "Nenhum trabalho novo: período=%s ids=%s inalterados=%s vazios=%s ausentes=%s",
            collection_set,
            len(items),
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
        log.info("Configuração recarregada; intervalo %s -> %s minuto(s)", old_interval, new_settings.homepicz_interval_minutes)
        return True

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.first_cycle_done.clear()
        self.thread = threading.Thread(target=self._loop, name="HomePiczScheduler", daemon=True)
        self.thread.start()
        log.info("Scheduler Home Picz iniciado; fila incremental e coleções por fotógrafo")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _wait_until_next_cycle(self, cycle_finished_at: float) -> None:
        while not self.stop_event.is_set():
            self._reload_settings_if_changed()
            interval_seconds = max(60, self.settings.homepicz_interval_minutes * 60)
            remaining = interval_seconds - (time.monotonic() - cycle_finished_at)
            if remaining <= 0:
                return
            self.stop_event.wait(min(CONFIG_POLL_SECONDS, remaining))

    def _loop(self) -> None:
        first_cycle = True
        while not self.stop_event.is_set():
            self._reload_settings_if_changed()
            started_at = datetime.now().isoformat(timespec="seconds")
            try:
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
            self._wait_until_next_cycle(cycle_finished_at)
