from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from . import homepicz_scheduler as scheduler
from .homepicz_scheduler_guard import next_poll_seconds


class ResponsiveHomePiczScheduler(scheduler.HomePiczScheduler):
    """Scheduler acordável, sem criar uma segunda varredura concorrente."""

    def __init__(self, settings, store, config_path=None):
        super().__init__(settings, store, config_path=config_path)
        self.wake_event = threading.Event()
        self.cycle_in_progress = threading.Event()
        self.cycle_generation = 0
        self.last_result: dict[str, object] | None = None

    def request_immediate_cycle(self) -> int:
        """Agenda um ciclo imediato.

        Se uma varredura já estiver em andamento, o pedido fica latente e executa
        logo depois, sem abrir um segundo leitor concorrente do Google Drive.
        """
        self.wake_event.set()
        return self.cycle_generation

    def is_cycle_in_progress(self) -> bool:
        return self.cycle_in_progress.is_set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _wait_until_next_cycle(self, cycle_finished_at: float) -> None:
        wait_started = time.monotonic()
        scheduled_seconds = max(1, int(next_poll_seconds()))
        while not self.stop_event.is_set():
            if self.wake_event.is_set():
                self.wake_event.clear()
                return

            settings_changed = self._reload_settings_if_changed()
            if settings_changed:
                elapsed = time.monotonic() - wait_started
                configured_seconds = max(60, int(self.settings.homepicz_interval_minutes or 1) * 60)
                scheduled_seconds = min(configured_seconds, max(1, int(next_poll_seconds())))
                if elapsed >= scheduled_seconds:
                    return

            remaining = scheduled_seconds - (time.monotonic() - wait_started)
            if remaining <= 0:
                return
            self.stop_event.wait(min(getattr(scheduler, "CONFIG_POLL_SECONDS", 1.0), remaining))

    def _loop(self) -> None:
        first_cycle = True
        while not self.stop_event.is_set():
            self._reload_settings_if_changed()
            started_at = datetime.now().isoformat(timespec="seconds")
            self.cycle_in_progress.set()
            try:
                result = scheduler.run_cycle(self.settings, self.store)
                self.last_result = result
                scheduler.log.info("Ciclo Home Picz concluído: %s", result)
            except Exception as exc:
                self.last_result = {
                    "status": "failed",
                    "at": started_at,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                scheduler.log.exception("Falha no ciclo automático Home Picz")
                scheduler._write_state(
                    self.settings,
                    {
                        "status": "failed",
                        "at": started_at,
                        "day_rollover_time": self.settings.homepicz_day_rollover_time,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            finally:
                self.cycle_in_progress.clear()
                self.cycle_generation += 1
                if first_cycle:
                    self.first_cycle_done.set()
                    first_cycle = False

            cycle_finished_at = time.monotonic()
            self._wait_until_next_cycle(cycle_finished_at)


def _scan_source_folder_without_video(folder: Path, recursive: bool, allowed_extensions: set[str]):
    """Varre RAWs sem entrar na pasta Video/Vídeo.

    A checagem é feita pelo nome antes de consultar o placeholder do Drive, então
    uma pasta Video quebrada não atrasa nem invalida o trabalho principal.
    """
    try:
        if not folder.exists():
            return scheduler.FolderScan("missing")
        if not folder.is_dir():
            return scheduler.FolderScan("missing", error="O caminho existe, mas não é uma pasta.")
    except PermissionError as exc:
        return scheduler.FolderScan("access_error", error=str(exc))
    except OSError as exc:
        return scheduler.FolderScan("drive_error", error=f"{type(exc).__name__}: {exc}")

    important_extensions = {value.lower().lstrip(".") for value in allowed_extensions}
    count = 0
    skipped_errors = 0
    broken_raws: list[dict[str, str]] = []

    def register_broken_raw(path: str, reason: str) -> None:
        broken_raws.append({"name": Path(path).name, "path": path, "error": reason})

    def visit(current: Path) -> None:
        nonlocal count, skipped_errors
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except PermissionError as exc:
            skipped_errors += 1
            scheduler.log.warning("Acesso negado ao listar subpasta %s: %s", current, exc)
            return
        except OSError as exc:
            skipped_errors += 1
            scheduler.log.warning(
                "Falha ao listar subpasta %s; a pasta principal continuará sendo processada: %s: %s",
                current,
                type(exc).__name__,
                exc,
            )
            return

        for entry in entries:
            entry_path = str(entry.path)
            suffix = Path(entry.name).suffix.lower().lstrip(".")

            # Regra explícita do Home Picz: Video/Vídeo não participa do RAW.
            if scheduler._normalized_key(entry.name) == "video":
                continue

            # Arquivos irrelevantes são descartados pelo nome antes de tocar no
            # placeholder do Google Drive.
            if suffix and suffix not in important_extensions:
                continue

            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        visit(Path(entry.path))
                    continue
            except (PermissionError, OSError) as exc:
                if suffix in important_extensions:
                    register_broken_raw(entry_path, f"{type(exc).__name__}: {exc}")
                continue

            if suffix not in important_extensions:
                continue

            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
                if size <= 0:
                    register_broken_raw(entry_path, "Arquivo RAW possui 0 bytes.")
                    continue
                count += 1
            except PermissionError as exc:
                register_broken_raw(entry_path, f"Acesso negado ao RAW: {exc}")
            except OSError as exc:
                register_broken_raw(entry_path, f"{type(exc).__name__}: {exc}")

    visit(folder)

    if count == 0 and broken_raws:
        return scheduler.FolderScan(
            "raw_error",
            count=0,
            error=f"{len(broken_raws)} arquivo(s) RAW não puderam ser lidos.",
            skipped_errors=skipped_errors,
            broken_raws=tuple(broken_raws),
        )
    if count == 0 and skipped_errors > 0:
        return scheduler.FolderScan(
            "access_error",
            count=0,
            error=f"{skipped_errors} subpasta(s) não puderam ser listadas.",
            skipped_errors=skipped_errors,
        )
    if count == 0:
        return scheduler.FolderScan("empty")
    if broken_raws or skipped_errors > 0:
        messages: list[str] = []
        if broken_raws:
            messages.append(f"{len(broken_raws)} RAW(s) problemático(s)")
        if skipped_errors:
            messages.append(f"{skipped_errors} subpasta(s) não puderam ser listadas")
        return scheduler.FolderScan(
            "partial",
            count=count,
            error="; ".join(messages) + ".",
            skipped_errors=skipped_errors,
            broken_raws=tuple(broken_raws),
        )
    return scheduler.FolderScan("ok", count=count)


def install_homepicz_runtime_responsiveness() -> None:
    if not getattr(scheduler._scan_source_folder, "_ignores_video", False):
        _scan_source_folder_without_video._ignores_video = True
        scheduler._scan_source_folder = _scan_source_folder_without_video

    if not getattr(scheduler.HomePiczScheduler, "_responsive_scheduler", False):
        ResponsiveHomePiczScheduler._responsive_scheduler = True
        scheduler.HomePiczScheduler = ResponsiveHomePiczScheduler
