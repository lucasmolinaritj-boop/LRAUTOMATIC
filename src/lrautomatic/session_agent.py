from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process

from .automation_control import consume_force_next, read_control
from .config import load_settings
from .homepicz_scheduler import HomePiczScheduler, run_cycle
from .store import JobStore

log = logging.getLogger("lrautomatic.session_agent")
CATALOG_POLL_SECONDS = 2


def _lightroom_processes() -> list[psutil.Process]:
    result: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (process.info.get("name") or "").lower()
            exe = (process.info.get("exe") or "").lower()
            if "lightroom" in name or exe.endswith("lightroom.exe"):
                result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _request_graceful_close(processes: list[psutil.Process]) -> None:
    pids = {process.pid for process in processes}

    def callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                title = win32gui.GetWindowText(hwnd)
                if title:
                    log.info("Solicitando fechamento da janela Lightroom: %s", title)
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            log.exception("Falha ao enviar WM_CLOSE")
        return True

    win32gui.EnumWindows(callback, None)


def close_lightroom(timeout_seconds: int = 90, force_timeout_seconds: int = 15) -> bool:
    processes = _lightroom_processes()
    if not processes:
        return True

    _request_graceful_close(processes)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        alive = [process for process in processes if process.is_running()]
        if not alive:
            log.info("Lightroom encerrado normalmente")
            return True
        time.sleep(1)

    log.warning("Lightroom não encerrou em %ss; encerramento forçado será usado", timeout_seconds)
    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=force_timeout_seconds)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return not any(process.is_running() for process in processes)


def _desired_catalog(settings) -> Path | None:
    try:
        raw = settings.desired_catalog_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.is_file() else None


def _active_catalog_hint(settings) -> Path | None:
    try:
        raw = (settings.control_dir / "agent_open_catalog.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return Path(raw) if raw else None


def _write_startup_state(settings, **values: object) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **values,
    }
    (settings.control_dir / "startup_flow.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def open_catalog(settings, catalog_path: Path) -> None:
    executable = settings.lightroom_executable
    if not executable or not executable.is_file():
        raise FileNotFoundError("Configure lightroom_executable com o Lightroom.exe correto")
    log.info("Abrindo Lightroom com catálogo %s", catalog_path)
    _write_startup_state(settings, status="opening_lightroom", catalog_path=str(catalog_path))
    subprocess.Popen([str(executable), str(catalog_path)], cwd=str(executable.parent), close_fds=True)
    (settings.control_dir / "agent_open_catalog.txt").write_text(str(catalog_path), encoding="utf-8")


def ensure_correct_catalog(settings) -> bool:
    """Abre ou troca para o catálogo solicitado.

    Retorna True quando uma nova instância do Lightroom foi aberta neste ciclo.
    """
    desired = _desired_catalog(settings)
    if not desired:
        return False

    running = bool(_lightroom_processes())
    last_opened = _active_catalog_hint(settings)
    same_catalog = last_opened is not None and last_opened.resolve() == desired.resolve()
    if running and same_catalog:
        return False

    if running:
        log.info("Troca de catálogo: %s -> %s", last_opened, desired)
        _write_startup_state(settings, status="switching_catalog", catalog_path=str(desired))
        if not close_lightroom():
            raise RuntimeError("Não foi possível encerrar o Lightroom para trocar o catálogo")
        time.sleep(3)

    open_catalog(settings, desired)
    return True


def _job_states(store: JobStore) -> tuple[bool, bool]:
    jobs = store.list()
    running = any(str(job.status) == "running" for job in jobs)
    queued = any(str(job.status) == "queued" for job in jobs)
    return running, queued


def run_forever(config_path: str | Path = "config.json") -> None:
    settings = load_settings(config_path)
    logging.basicConfig(
        filename=settings.logs_dir / "session-agent.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = JobStore(settings)
    scheduler: HomePiczScheduler | None = None
    forced_job_active = False

    def start_scheduler() -> HomePiczScheduler:
        instance = HomePiczScheduler(settings, store, config_path=config_path)
        instance.start()
        return instance

    control = read_control(settings)
    if not control["paused"]:
        scheduler = start_scheduler()
        _write_startup_state(settings, status="checking_homepicz_now")
    else:
        _write_startup_state(settings, status="automation_paused")

    log.info(
        "Agente iniciado: controle pelo Monitor ativo; catálogo será conferido a cada %ss",
        CATALOG_POLL_SECONDS,
    )

    lightroom_seen = bool(_lightroom_processes())
    try:
        while True:
            try:
                control = read_control(settings)
                paused = bool(control["paused"])
                force_pending = bool(control["force_next_requested"])
                running_job, queued_job = _job_states(store)

                if paused and scheduler is not None:
                    scheduler.stop()
                    scheduler = None
                    log.info("Automação pausada pelo Monitor; job em execução não foi interrompido")
                    _write_startup_state(
                        settings,
                        status="automation_paused_waiting_running" if running_job else "automation_paused",
                    )
                elif not paused and scheduler is None:
                    scheduler = start_scheduler()
                    log.info("Automação retomada pelo Monitor")
                    _write_startup_state(settings, status="automation_resumed")

                if force_pending and not running_job:
                    if scheduler is not None:
                        scheduler.stop()
                        scheduler = None
                    result = run_cycle(settings, store)
                    forced_job_active = True
                    consume_force_next(
                        settings,
                        message=f"Ciclo imediato executado: {result.get('status', 'concluído')}.",
                    )
                    log.info("Próximo job forçado pelo Monitor: %s", result)
                    _write_startup_state(settings, status="forced_cycle_completed", result=result)
                    if not paused:
                        scheduler = start_scheduler()
                    running_job, queued_job = _job_states(store)

                may_manage_catalog = (not paused) or running_job or forced_job_active
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

                if forced_job_active and not running_job and not queued_job:
                    forced_job_active = False
                    log.info("Job forçado concluído; retornando ao estado normal de controle")
            except Exception:
                _write_startup_state(settings, status="failed", error="Consulte session-agent.log")
                log.exception("Falha no ciclo do agente")
            time.sleep(CATALOG_POLL_SECONDS)
    finally:
        if scheduler is not None:
            scheduler.stop()


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    run_forever(config_path)


if __name__ == "__main__":
    main()
