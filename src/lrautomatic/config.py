from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 45821
    api_key: str = "change-me"
    data_dir: Path = Path(os.path.expandvars(r"%LOCALAPPDATA%\LRAutomatic"))
    catalog_template: Path | None = None
    catalog_output_root: Path | None = None
    lightroom_executable: Path | None = None
    catalog_naming_template: str = "{date} scout"
    catalog_date_format: str = "%d%m%Y"
    catalog_date_source: str = "earliest_file"
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

    def validate(self, *, check_paths: bool = False) -> list[str]:
        errors: list[str] = []
        if not self.host.strip():
            errors.append("Host da API não pode ficar vazio.")
        if not 1 <= self.port <= 65535:
            errors.append("Porta da API deve estar entre 1 e 65535.")
        if not self.api_key.strip() or self.api_key == "change-me":
            errors.append("Defina uma chave de API forte.")
        elif len(self.api_key) < 24:
            errors.append("A chave de API deve ter pelo menos 24 caracteres.")
        if "{date}" not in self.catalog_naming_template:
            errors.append("O modelo de nome do catálogo deve conter {date}.")
        if self.catalog_date_source not in {"earliest_file", "today"}:
            errors.append("A origem da data deve ser earliest_file ou today.")
        if self.homepicz_interval_minutes < 1:
            errors.append("O intervalo Home Picz deve ser de pelo menos 1 minuto.")

        if check_paths:
            if self.catalog_template and not self.catalog_template.is_file():
                errors.append("O catálogo-modelo não foi encontrado.")
            if self.catalog_output_root and not self.catalog_output_root.exists():
                errors.append("A pasta de destino dos catálogos não existe.")
            if self.lightroom_executable and not self.lightroom_executable.is_file():
                errors.append("O executável do Lightroom não foi encontrado.")
            if not self.homepicz_photos_root.exists():
                errors.append("A pasta de fotos Home Picz não existe.")
        return errors

    def to_json_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, Path):
                result[field.name] = str(value)
            elif value is None:
                result[field.name] = None
            else:
                result[field.name] = value
        return result


SETTING_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("API e serviço", ("host", "port", "api_key", "data_dir")),
    ("Catálogos e Lightroom", (
        "catalog_template", "catalog_output_root", "lightroom_executable",
        "catalog_naming_template", "catalog_date_format", "catalog_date_source",
    )),
    ("Automação Home Picz", (
        "homepicz_appscript_url", "homepicz_photos_root", "homepicz_interval_minutes",
        "homepicz_preset_name", "homepicz_smart_previews", "homepicz_recursive",
    )),
)

SETTING_LABELS: dict[str, str] = {
    "host": "Host da API",
    "port": "Porta da API",
    "api_key": "Chave da API",
    "data_dir": "Pasta de dados",
    "catalog_template": "Catálogo-modelo (.lrcat)",
    "catalog_output_root": "Pasta de saída dos catálogos",
    "lightroom_executable": "Executável do Lightroom",
    "catalog_naming_template": "Modelo do nome do catálogo",
    "catalog_date_format": "Formato da data",
    "catalog_date_source": "Origem da data",
    "homepicz_appscript_url": "URL do Google Apps Script",
    "homepicz_photos_root": "Pasta das fotos Home Picz",
    "homepicz_interval_minutes": "Intervalo de consulta (minutos)",
    "homepicz_preset_name": "Preset padrão Home Picz",
    "homepicz_smart_previews": "Criar Smart Previews",
    "homepicz_recursive": "Incluir subpastas",
}

PATH_SETTINGS = {
    "data_dir", "catalog_template", "catalog_output_root",
    "lightroom_executable", "homepicz_photos_root",
}
BOOL_SETTINGS = {"homepicz_smart_previews", "homepicz_recursive"}
INT_SETTINGS = {"port", "homepicz_interval_minutes"}
OPTIONAL_SETTINGS = {"catalog_template", "catalog_output_root", "lightroom_executable", "homepicz_appscript_url", "homepicz_preset_name"}


def generate_api_key() -> str:
    return secrets.token_urlsafe(48)


def _optional_path(value: str | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def _required_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "sim", "on"}:
        return True
    if normalized in {"0", "false", "no", "não", "nao", "off", ""}:
        return False
    raise ValueError(f"Valor booleano inválido: {value!r}")


def settings_from_dict(raw: dict[str, Any]) -> Settings:
    date_source = str(raw.get("catalog_date_source", "earliest_file")).strip().lower()
    settings = Settings(
        host=str(raw.get("host", "127.0.0.1")).strip(),
        port=int(raw.get("port", 45821)),
        api_key=str(raw.get("api_key", os.getenv("LRAUTOMATIC_API_KEY", "change-me"))).strip(),
        data_dir=_required_path(raw.get("data_dir", r"%LOCALAPPDATA%\LRAutomatic")),
        catalog_template=_optional_path(raw.get("catalog_template")),
        catalog_output_root=_optional_path(raw.get("catalog_output_root")),
        lightroom_executable=_optional_path(raw.get("lightroom_executable")),
        catalog_naming_template=str(raw.get("catalog_naming_template", "{date} scout")).strip(),
        catalog_date_format=str(raw.get("catalog_date_format", "%d%m%Y")).strip(),
        catalog_date_source=date_source,
        homepicz_appscript_url=(str(raw["homepicz_appscript_url"]).strip() if raw.get("homepicz_appscript_url") else None),
        homepicz_photos_root=_required_path(raw.get("homepicz_photos_root", r"M:\Meu Drive\Homepicz\Fotos do dia")),
        homepicz_interval_minutes=int(raw.get("homepicz_interval_minutes", 30)),
        homepicz_preset_name=(str(raw["homepicz_preset_name"]).strip() if raw.get("homepicz_preset_name") else None),
        homepicz_smart_previews=_parse_bool(raw.get("homepicz_smart_previews"), True),
        homepicz_recursive=_parse_bool(raw.get("homepicz_recursive"), False),
    )
    errors = settings.validate(check_paths=False)
    if errors:
        raise ValueError("Configuração inválida:\n- " + "\n- ".join(errors))
    return settings


def load_settings(path: str | Path = "config.json") -> Settings:
    config_path = Path(path)
    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido em {config_path}: linha {exc.lineno}, coluna {exc.colno}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("O arquivo de configuração precisa conter um objeto JSON.")
        raw = parsed
    settings = settings_from_dict(raw)
    settings.ensure_dirs()
    return settings


def save_settings(settings: Settings, path: str | Path = "config.json") -> Path:
    errors = settings.validate(check_paths=False)
    if errors:
        raise ValueError("Configuração inválida:\n- " + "\n- ".join(errors))

    config_path = Path(path).expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.to_json_dict(), ensure_ascii=False, indent=2) + "\n"

    fd, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, config_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    settings.ensure_dirs()
    return config_path
