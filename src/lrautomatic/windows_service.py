from __future__ import annotations

import logging
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
from .homepicz_scheduler import HomePiczScheduler
from .store import JobStore


class LRAutomaticService(win32serviceutil.ServiceFramework):
    _svc_name_ = "LRAutomatic"
    _svc_display_name_ = "LRAutomatic Home Picz"
    _svc_description_ = "API local, fila e agendador Home Picz para o Lightroom Classic."

    def __init__(self, args):
        super().__init__(args)
        self.stop_handle = win32event.CreateEvent(None, 0, 0, None)
        self.server: uvicorn.Server | None = None
        self.scheduler: HomePiczScheduler | None = None
        self.thread: threading.Thread | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.scheduler:
            self.scheduler.stop()
        if self.server:
            self.server.should_exit = True
        win32event.SetEvent(self.stop_handle)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("LRAutomatic iniciando")
        config_path = Path(sys.executable).resolve().parent.parent / "config.json"
        if not config_path.exists():
            config_path = Path.cwd() / "config.json"
        settings = load_settings(config_path)
        logging.basicConfig(
            filename=settings.logs_dir / "windows-service.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        store = JobStore(settings)
        self.scheduler = HomePiczScheduler(settings, store)
        self.scheduler.start()
        config = uvicorn.Config(create_app(config_path), host=settings.host, port=settings.port, log_level="info")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="LRAutomaticAPI", daemon=True)
        self.thread.start()
        win32event.WaitForSingleObject(self.stop_handle, win32event.INFINITE)
        if self.thread:
            self.thread.join(timeout=15)
        servicemanager.LogInfoMsg("LRAutomatic encerrado")


def main() -> None:
    win32serviceutil.HandleCommandLine(LRAutomaticService)


if __name__ == "__main__":
    main()
