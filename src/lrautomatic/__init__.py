__version__ = "0.1.0"


def install_homepicz_queue_guard() -> None:
    """Instala a guarda da fila sem alterar o scheduler grande diretamente."""
    try:
        from . import homepicz_scheduler as scheduler
        from .homepicz_scheduler_guard import guarded_cycle
    except Exception:
        return

    original = getattr(scheduler, "run_cycle", None)
    if original is None or getattr(original, "_homepicz_guarded", False):
        return

    def wrapped(settings, store, now=None):
        return guarded_cycle(store, original, settings, now)

    wrapped._homepicz_guarded = True
    scheduler.run_cycle = wrapped


install_homepicz_queue_guard()
