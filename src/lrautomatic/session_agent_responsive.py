from __future__ import annotations

import logging
import time
from pathlib import Path

from .automation_control import consume_force_next, force_once_flag_path, read_control
from .config import load_settings
from .homepicz_scheduler import HomePiczScheduler
from .session_agent import (
    CATALOG_POLL_SECONDS,
    _job_states,
    _lightroom_processes,
    _write_startup_state,
    ensure_correct_catalog,
    log,
)
from .store import JobStore


def _clear_force_flag(settings) -> None:
    try:
        force_once_flag_path(settings).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("Não foi possível limpar o marcador de execução forçada")


def run_forever_responsive(config_path: str | Path = "config.json") -> None:
    settings = load_settings(config_path)
    logging.basicConfig(
        filename=settings.logs_dir / "session-agent.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = JobStore(settings)
    scheduler = HomePiczScheduler(settings, store, config_path=config_path)
    scheduler.start()

    forced_job_active = False
    forced_cycle_baseline: int | None = None

    control = read_control(settings)
    _write_startup_state(
        settings,
        status="automation_paused_queue_active" if control["paused"] else "checking_homepicz_now",
        message=(
            "Criação de jobs ativa; início de novos jobs pausado."
            if control["paused"]
            else "Automação ativa."
        ),
    )
    log.info(
        "Agente responsivo iniciado: força acorda o scheduler sem duplicar varreduras; catálogo conferido a cada %ss",
        CATALOG_POLL_SECONDS,
    )

    lightroom_seen = bool(_lightroom_processes())
    last_paused = bool(control["paused"])

    try:
        while True:
            try:
                control = read_control(settings)
                paused = bool(control["paused"])
                force_pending = bool(control["force_next_requested"])
                running_job, queued_job = _job_states(store)

                if paused != last_paused:
                    if paused:
                        log.info("Início de novos jobs pausado; scheduler continuará criando tarefas")
                        _write_startup_state(
                            settings,
                            status="automation_paused_waiting_running" if running_job else "automation_paused_queue_active",
                            message="Jobs continuam sendo criados e enfileirados; nenhum novo job será iniciado.",
                        )
                    else:
                        log.info("Início de jobs retomado pelo Monitor")
                        _write_startup_state(settings, status="automation_resumed")
                    last_paused = paused

                if force_pending and not running_job:
                    if queued_job:
                        forced_job_active = True
                        if not paused:
                            _clear_force_flag(settings)
                        consume_force_next(
                            settings,
                            message="Job já estava na fila; início solicitado ao plugin do Lightroom.",
                        )
                        log.info("Forçar próximo: job já enfileirado; nenhuma nova varredura foi aberta")
                        _write_startup_state(settings, status="forced_existing_job_released")
                    elif forced_cycle_baseline is None:
                        baseline = int(getattr(scheduler, "cycle_generation", 0))
                        request_immediate = getattr(scheduler, "request_immediate_cycle", None)
                        if callable(request_immediate):
                            request_immediate()
                            forced_cycle_baseline = baseline
                            forced_job_active = True
                            if not paused:
                                _clear_force_flag(settings)
                            consume_force_next(
                                settings,
                                message="Varredura imediata solicitada; aguardando o ciclo atual ou o próximo ciclo acordado.",
                            )
                            state = (
                                "forced_cycle_queued_after_current"
                                if getattr(scheduler, "is_cycle_in_progress", lambda: False)()
                                else "forced_cycle_woken"
                            )
                            log.info("Forçar próximo: scheduler acordado sem ciclo concorrente; baseline=%s", baseline)
                            _write_startup_state(settings, status=state)

                if forced_cycle_baseline is not None:
                    generation = int(getattr(scheduler, "cycle_generation", 0))
                    if generation > forced_cycle_baseline:
                        result = getattr(scheduler, "last_result", None) or {}
                        forced_cycle_baseline = None
                        running_job, queued_job = _job_states(store)
                        if not running_job and not queued_job:
                            forced_job_active = False
                            _clear_force_flag(settings)
                        log.info("Ciclo solicitado pelo Monitor concluído: %s", result)
                        _write_startup_state(settings, status="forced_cycle_completed", result=result)

                may_manage_catalog = (
                    (not paused)
                    or running_job
                    or queued_job
                    or forced_job_active
                    or forced_cycle_baseline is not None
                )
                if may_manage_catalog:
                    opened_now = ensure_correct_catalog(settings)
                    running_now = bool(_lightroom_processes())
                    if opened_now:
                        _write_startup_state(
                            settings,
                            status="lightroom_opened_waiting_plugin",
                            message="O plugin deve consumir a fila automaticamente em poucos segundos.",
                        )
                        log.info("Lightroom aberto; aguardando o plugin consumir a fila automaticamente")
                    elif running_now and not lightroom_seen:
                        _write_startup_state(
                            settings,
                            status="lightroom_detected_waiting_plugin",
                            message="O plugin deve consumir a fila automaticamente em poucos segundos.",
                        )
                        log.info("Lightroom detectado; o plugin deve iniciar a importação em poucos segundos")
                    lightroom_seen = running_now

                if (
                    forced_job_active
                    and forced_cycle_baseline is None
                    and not running_job
                    and not queued_job
                ):
                    forced_job_active = False
                    _clear_force_flag(settings)
                    log.info("Fluxo forçado concluído; retornando ao estado normal de controle")
            except Exception:
                _write_startup_state(settings, status="failed", error="Consulte session-agent.log")
                log.exception("Falha no ciclo do agente responsivo")

            time.sleep(CATALOG_POLL_SECONDS)
    finally:
        scheduler.stop()
