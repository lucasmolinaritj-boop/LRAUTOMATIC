__version__ = "0.1.0"


def install_homepicz_queue_guard() -> None:
    """Instala a guarda da fila sem alterar o scheduler grande diretamente."""
    try:
        from . import homepicz_scheduler as scheduler
        from .homepicz_scheduler_guard import guarded_cycle, next_poll_seconds
    except Exception:
        return

    original = getattr(scheduler, "run_cycle", None)
    if original is not None and not getattr(original, "_homepicz_guarded", False):
        def wrapped(settings, store, now=None):
            return guarded_cycle(store, original, settings, now)

        wrapped._homepicz_guarded = True
        scheduler.run_cycle = wrapped

    scheduler_class = getattr(scheduler, "HomePiczScheduler", None)
    if scheduler_class is None or getattr(scheduler_class, "_homepicz_wait_guarded", False):
        return

    def guarded_wait(self, cycle_finished_at):
        # O intervalo entre jobs é decidido pela guarda usando finished_at.
        # Aqui apenas aguardamos o próximo instante de polling sugerido.
        import time

        wait_started = time.monotonic()
        while not self.stop_event.is_set():
            self._reload_settings_if_changed()
            remaining = next_poll_seconds() - (time.monotonic() - wait_started)
            if remaining <= 0:
                return
            self.stop_event.wait(min(getattr(scheduler, "CONFIG_POLL_SECONDS", 1.0), remaining))

    scheduler_class._wait_until_next_cycle = guarded_wait
    scheduler_class._homepicz_wait_guarded = True


install_homepicz_queue_guard()
