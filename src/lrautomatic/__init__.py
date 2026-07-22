__version__ = "0.1.0"


def install_homepicz_queue_guard() -> None:
    """Instala a guarda da fila sem alterar o scheduler grande diretamente."""
    try:
        from . import homepicz_scheduler as scheduler
        from .homepicz_scheduler_guard import guarded_cycle, next_poll_seconds
    except Exception:
        return

    def safe_is_homepicz_job(job) -> bool:
        request = getattr(job, "request", None)
        collection_set = getattr(request, "collection_set", None)
        return str(collection_set or "").startswith(
            getattr(scheduler, "HOME_PICZ_COLLECTION_PREFIX", "Home Picz - ")
        )

    scheduler._is_homepicz_job = safe_is_homepicz_job

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
        import time

        wait_started = time.monotonic()
        scheduled_seconds = max(1, int(next_poll_seconds()))
        while not self.stop_event.is_set():
            settings_changed = self._reload_settings_if_changed()
            if settings_changed:
                elapsed = time.monotonic() - wait_started
                configured_seconds = max(60, int(self.settings.homepicz_interval_minutes or 1) * 60)
                current_suggestion = max(1, int(next_poll_seconds()))
                scheduled_seconds = min(configured_seconds, current_suggestion)
                if elapsed >= scheduled_seconds:
                    return

            remaining = scheduled_seconds - (time.monotonic() - wait_started)
            if remaining <= 0:
                return
            self.stop_event.wait(min(getattr(scheduler, "CONFIG_POLL_SECONDS", 1.0), remaining))

    scheduler_class._wait_until_next_cycle = guarded_wait
    scheduler_class._homepicz_wait_guarded = True


install_homepicz_queue_guard()

try:
    from .homepicz_editor_features import install_homepicz_editor_features

    install_homepicz_editor_features()
except Exception:
    pass

try:
    from .raw_creation_time import install_raw_creation_time

    install_raw_creation_time()
except Exception:
    pass
