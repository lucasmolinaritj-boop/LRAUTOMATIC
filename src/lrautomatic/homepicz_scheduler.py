from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .catalogs import create_catalog
from .config import Settings
from .models import ImportJobRequest, ImportSource
from .store import JobStore

log = logging.getLogger("lrautomatic.homepicz")


@dataclass(frozen=True, slots=True)
class ImportWindow:
    start: date
    end: date

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%d-%m-%Y")
        return f"{self.start:%d-%m-%Y}_a_{self.end:%d-%m-%Y}"


def previous_business_window(today: date | None = None) -> ImportWindow:
    today = today or date.today()
    if today.weekday() == 0:  # segunda-feira: sexta, sábado e domingo
        return ImportWindow(today - timedelta(days=3), today - timedelta(days=1))
    yesterday = today - timedelta(days=1)
    return ImportWindow(yesterday, yesterday)


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
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_cycle(settings: Settings, store: JobStore) -> dict[str, object]:
    window = previous_business_window()
    catalog_path = _catalog_for_window(settings, window)
    ids = _fetch_ids(settings, window)
    sources: list[ImportSource] = []
    missing: list[str] = []
    for work_id in ids:
        folder = settings.homepicz_photos_root / work_id
        if folder.is_dir():
            sources.append(ImportSource(path=str(folder), collection=work_id))
        else:
            missing.append(work_id)

    result: dict[str, object] = {
        "status": "completed",
        "at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "catalog_path": str(catalog_path),
        "ids": len(ids),
        "valid_sources": len(sources),
        "missing_ids": missing,
    }
    if sources:
        request = ImportJobRequest(
            sources=sources,
            collection_set=f"Home Picz - {window.label}",
            recursive=settings.homepicz_recursive,
            build_smart_previews=settings.homepicz_smart_previews,
            develop_preset_name=settings.homepicz_preset_name,
            duplicate_policy="skip",
        )
        job = store.create(request)
        result["job_id"] = job.job_id
    _write_state(settings, result)
    return result


class HomePiczScheduler:
    def __init__(self, settings: Settings, store: JobStore):
        self.settings = settings
        self.store = store
        self.stop_event = threading.Event()
        self.first_cycle_done = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.first_cycle_done.clear()
        self.thread = threading.Thread(target=self._loop, name="HomePiczScheduler", daemon=True)
        self.thread.start()
        log.info("Scheduler Home Picz iniciado; primeira verificação será imediata")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _loop(self) -> None:
        first_cycle = True
        while not self.stop_event.is_set():
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
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            finally:
                if first_cycle:
                    self.first_cycle_done.set()
                    first_cycle = False

            interval_seconds = max(60, self.settings.homepicz_interval_minutes * 60)
            next_run = datetime.now() + timedelta(seconds=interval_seconds)
            log.info("Próxima verificação Home Picz em %s", next_run.isoformat(timespec="seconds"))
            self.stop_event.wait(interval_seconds)
