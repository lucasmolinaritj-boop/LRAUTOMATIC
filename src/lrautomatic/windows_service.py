from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import servicemanager
import uvicorn
import win32event
import win32service
import win32serviceutil

from .api import create_app
from .config import load_settings

SERVICE_NAME = "LRAutomatic"
CONFIG_ENV_VAR = "LRAUTOMATIC_CONFIG"


def _service_registry_config_path() -> Path | None:
    """Read the config path written by instalar_servidor.bat.

    A Windows service normally starts with System32 as its working directory,
    so relative paths and Path.cwd() are not reliable here.
    """
    try:
        import winreg

        key_path = rf"SYSTEM\CurrentControlSet\Services\{SERVICE_NAME}\Parameters"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "ConfigPath")
        path = Path(os.path.expandvars(str(value))).expanduser()
        return path.resolve()
    except (FileNotFoundError, OSError, ValueError):
        return None


def resolve_config_path() -> Path:
    candidates: list[Path] = []

    configured = os.environ.get(CONFIG_ENV_VAR)
    if configured:
        candidates.append(Path(os.path.expandvars(configured)).expanduser())

    registry_path = _service_registry_config_path()
    if registry_path is not None:
        candidates.append(registry_path)

    executable = Path(sys.executable).resolve()
    candidates.extend(
        [
            executable.parent.parent.parent / "config.json",
            executable.parent.parent / "config.json",
            Path(__file__).resolve().parents[2] / "config.json",
            Path.cwd() / "config.json",
        ]
    )

    checked: list[str] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        checked.append(str(resolved))
        if resolved.is_file():
            return resolved

    raise FileNotFoundError(
        "config.json do LRAutomatic nao encontrado. Caminhos verificados: "
        + " | ".join(checked)
    )


class LRAutomaticService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "LRAutomatic API"
    _svc_description_ = "Servidor local e API do LRAutomatic para o Lightroom Classic."

    def __init__(self, args):
        super().__init__(args)
        self.stop_handle = win32event.CreateEvent(None, 0, 0, None)
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.server:
            self.server.should_exit = True
        win32event.SetEvent(self.stop_handle)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("LRAutomatic API iniciando")
        try:
            config_path = resolve_config_path()
            settings = load_settings(config_path)
            settings.ensure_dirs()
            logging.basicConfig(
                filename=settings.logs_dir / "windows-service.log",
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(name)s %(message)s",
                force=True,
            )
            logging.info("Servico iniciado com configuracao em %s", config_path)
            config = uvicorn.Config(
                create_app(config_path),
                host=settings.host,
                port=settings.port,
                log_level="info",
            )
            self.server = uvicorn.Server(config)
            self.thread = threading.Thread(
                target=self.server.run,
                name="LRAutomaticAPI",
                daemon=True,
            )
            self.thread.start()
            win32event.WaitForSingleObject(self.stop_handle, win32event.INFINITE)
            if self.thread:
                self.thread.join(timeout=15)
            servicemanager.LogInfoMsg("LRAutomatic API encerrado")
        except Exception:
            logging.exception("Falha fatal no servico LRAutomatic")
            servicemanager.LogErrorMsg(
                "LRAutomatic API falhou ao iniciar. Consulte windows-service.log e o Visualizador de Eventos."
            )
            raise


def main() -> None:
    win32serviceutil.HandleCommandLine(LRAutomaticService)


if __name__ == "__main__":
    main()
