from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 45821
    api_key: str = "change-me"
    data_dir: Path = Path(os.path.expandvars(r"%LOCALAPPDATA%\LRAutomatic"))
    catalog_template: Path | None = None
    catalog_output_root: Path | None = None
    lightroom_executable: Path | None = None
    homepicz_appscript_url: str | None = None
    homepicz_photos_root: Path = Path(r"M:\Meu Drive\Homepicz\Fotos do dia")
    homepicz_interval_minutes: int = 30
    homepicz_preset_name: str | None = None
    homepicz_smart_previews: bool = True
    homepicz_recursive: bool = False

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def responses_dir(self) -> Path:
        return self.data_dir / "responses"

    @property
    def control_dir(self) -> Path:
        return self.data_dir / "control"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def desired_catalog_file(self) -> Path:
        return self.control_dir / "desired_catalog.txt"

    @property
    def scheduler_state_file(self) -> Path:
        return self.control_dir / "homepicz_scheduler_state.json"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.jobs_dir, self.responses_dir, self.control_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser().resolve()


def load_settings(path: str | Path = "config.json") -> Settings:
    config_path = Path(path)
    raw: dict = {}
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))

    settings = Settings(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 45821)),
        api_key=str(raw.get("api_key", os.getenv("LRAUTOMATIC_API_KEY", "change-me"))),
        data_dir=Path(os.path.expandvars(raw.get("data_dir", r"%LOCALAPPDATA%\LRAutomatic"))).expanduser().resolve(),
        catalog_template=_optional_path(raw.get("catalog_template")),
        catalog_output_root=_optional_path(raw.get("catalog_output_root")),
        lightroom_executable=_optional_path(raw.get("lightroom_executable")),
        homepicz_appscript_url=raw.get("homepicz_appscript_url"),
        homepicz_photos_root=Path(os.path.expandvars(raw.get("homepicz_photos_root", r"M:\Meu Drive\Homepicz\Fotos do dia"))).expanduser().resolve(),
        homepicz_interval_minutes=max(1, int(raw.get("homepicz_interval_minutes", 30))),
        homepicz_preset_name=raw.get("homepicz_preset_name"),
        homepicz_smart_previews=bool(raw.get("homepicz_smart_previews", True)),
        homepicz_recursive=bool(raw.get("homepicz_recursive", False)),
    )
    settings.ensure_dirs()
    return settings
