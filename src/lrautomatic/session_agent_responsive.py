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

PAUSED_CONTROL_POLL_SECONDS = 2.0


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

    # A pausa é aplicada antes do start para impedir até a primeira consulta ao
    # Apps Script ou a primeira varredura do Google Drive ao iniciar o Windows.
    control = read_control(settings)
    initial_paused = bool(control["paused"])
    scheduler = HomePiczScheduler(settings, store, config_path=config_path)
    set_scheduler_paused = getattr(scheduler, "set_paused", None)
    if callable(set_scheduler_paused):
        set_scheduler_paused(initial_paused)
    scheduler.start()

    forced_job_active = False
    forced_cycle_baseline: int | None = None

    _write_startup_state(
        settings,
        status="automation_fully_paused" if initial_paused else "checking_homepicz_now",
        message=(
            "Automação totalmente pausada: sem consultas, varreduras ou novos jobs."
            if initial_paused
            else "Automação ativa."
        ),
    )
    log.info(
        "Agente responsivo iniciado: pausa suspende API e disco; força acorda um único ciclo; controle conferido a cada %.1fs",
        PAUSED_CONTROL_POLL_SECONDS,
    )

    lightroom_seen = bool(_lightroom_processes())
    last_paused = initial_paused

    try:
        while True:
            paused = last_paused
            force_pending = False
            running_job = False
            queued_job = False
            try:
                control = read_control(settings)
                paused = bool(control["paused"])
                force_pending = bool(control["force_next_requested"])
                pause_changed = paused != last_paused

                if pause_changed:
                    set_scheduler_paused = getattr(scheduler, "set_paused", None)
                    if callable(set_scheduler_paused):
                        set_scheduler_paused(paused)
                    if paused:
                        log.info("Automação totalmente pausada; scheduler, API e varreduras suspensos")
                        _write_startup_state(
                            settings,
                            status="automation_fully_paused",
                            message="Sem consultas ao Apps Script, sem leitura das pastas RAW e sem novos jobs.",
                        )
                    else:
                        log.info("Automação retomada; scheduler acordado para checagem imediata")
                        _write_startup_state(
                            settings,
                            status="automation_resumed_checking_now",
                            message="Automação retomada; checagem imediata iniciada.",
                        )
                    last_paused = paused

                # Em pausa total e sem Forçar, não lista nem os arquivos JSON dos jobs.
                # O único I/O periódico é a leitura do pequeno arquivo de controle.
                needs_job_state = (
                    not paused
                    or force_pending
                    or forced_job_active
                    or forced_cycle_baseline is not None
                    or pause_changed
                )
                if needs_job_state:
                    running_job, queued_job = _job_states(store)

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
                            request_immediate(allow_while_paused=paused)
                            forced_cycle_baseline = baseline
                            forced_job_active = True
                            if not paused:
                                _clear_force_flag(settings)
                            consume_force_next(
                                settings,
                                message="Um único ciclo imediato foi solicitado.",
                            )
                            state = (
                                "forced_cycle_queued_after_current"
                                if getattr(scheduler, "is_cycle_in_progress", lambda: False)()
                                else "forced_cycle_woken"
                            )
                            log.info(
                                "Forçar próximo: scheduler acordado sem ciclo concorrente; pausado=%s baseline=%s",
                                paused,
                                baseline,
                            )
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

                # Pausado não abre Lightroom por causa de job apenas enfileirado.
                # Um job já em execução pode terminar, e o fluxo Forçar continua válido.
                may_manage_catalog = (
                    not paused
                    or running_job
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

            sleep_seconds = (
                PAUSED_CONTROL_POLL_SECONDS
                if paused and not force_pending and not forced_job_active
                else CATALOG_POLL_SECONDS
            )
            time.sleep(sleep_seconds)
    finally:
        scheduler.stop()
