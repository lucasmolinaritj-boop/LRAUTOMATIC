from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import homepicz_scheduler as scheduler
from .config import load_settings
from .homepicz_scheduler_guard import next_poll_seconds
from .store import JobStore


_config_path: Path | None = None


def _seconds_until_rollover(settings, now: datetime | None = None) -> int:
    current = now or datetime.now()
    target = datetime.combine(current.date(), settings.day_rollover_time)
    if target <= current:
        target += timedelta(days=1)
    return max(1, math.ceil((target - current).total_seconds()))


def install_homepicz_rollover_reliability() -> None:
    original_class = scheduler.HomePiczScheduler
    if not getattr(original_class, "_rollover_reliable", False):

        class RolloverReliableScheduler(original_class):
            _rollover_reliable = True

            def __init__(self, settings, store, config_path=None):
                global _config_path
                super().__init__(settings, store, config_path=config_path)
                if self.config_path is not None:
                    _config_path = self.config_path

            def _wait_until_next_cycle(self, cycle_finished_at: float) -> None:
                scheduled_seconds = min(
                    max(1, int(next_poll_seconds())),
                    _seconds_until_rollover(self.settings),
                )
                while not self.stop_event.is_set():
                    if getattr(self, "pause_event", None) is not None and self.pause_event.is_set():
                        return
                    if getattr(self, "wake_event", None) is not None and self.wake_event.is_set():
                        self.wake_event.clear()
                        return

                    if self._reload_settings_if_changed():
                        # Qualquer alteração relevante no config deve ser avaliada
                        # imediatamente. Isso inclui mudar a virada de 20h para 14h.
                        return

                    remaining = scheduled_seconds - (time.monotonic() - cycle_finished_at)
                    if remaining <= 0:
                        return
                    wait_event = getattr(self, "wake_event", self.stop_event)
                    wait_event.wait(
                        min(getattr(scheduler, "CONFIG_POLL_SECONDS", 1.0), remaining)
                    )

        scheduler.HomePiczScheduler = RolloverReliableScheduler

    original_run_cycle = scheduler.run_cycle
    if not getattr(original_run_cycle, "_reloads_current_config", False):

        def run_cycle_with_current_config(settings, store, now=None):
            if _config_path is not None:
                try:
                    current_settings = load_settings(_config_path)
                    if current_settings.data_dir != settings.data_dir:
                        store = JobStore(current_settings)
                    settings = current_settings
                except Exception:
                    scheduler.log.exception(
                        "Falha ao recarregar config antes do ciclo; "
                        "a última configuração válida será usada"
                    )
            return original_run_cycle(settings, store, now)

        run_cycle_with_current_config._reloads_current_config = True
        scheduler.run_cycle = run_cycle_with_current_config
