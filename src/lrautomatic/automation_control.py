from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

CONTROL_FILENAME = "automation_control.json"
PAUSE_FLAG_FILENAME = "automation_paused.flag"
FORCE_ONCE_FLAG_FILENAME = "automation_force_once.flag"


def control_path(settings: Any) -> Path:
    return settings.control_dir / CONTROL_FILENAME


def runner_control_dir(settings: Any) -> Path:
    # O plugin Lightroom usa a pasta fixa compartilhada da fila. Estes marcadores
    # precisam ficar ao lado dela para continuarem visíveis mesmo se data_dir mudar.
    return settings.jobs_dir.parent / "control"


def pause_flag_path(settings: Any) -> Path:
    return runner_control_dir(settings) / PAUSE_FLAG_FILENAME


def force_once_flag_path(settings: Any) -> Path:
    return runner_control_dir(settings) / FORCE_ONCE_FLAG_FILENAME


def _write_flag(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _remove_flag(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _default() -> dict[str, Any]:
    return {
        "paused": False,
        "force_next_requested": False,
        "updated_at": None,
        "updated_by": None,
        "message": "Automação ativa",
    }


def read_control(settings: Any) -> dict[str, Any]:
    path = control_path(settings)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        value = _default()
    if not isinstance(value, dict):
        value = _default()
    result = _default()
    result.update(value)

    # O marcador compartilhado é a autoridade para o runner do Lightroom.
    result["paused"] = pause_flag_path(settings).is_file() or bool(result.get("paused"))
    result["force_next_requested"] = bool(result.get("force_next_requested"))
    return result


def write_control(settings: Any, value: dict[str, Any]) -> dict[str, Any]:
    path = control_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _default()
    payload.update(value)
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(encoded, encoding="utf-8")
    os.replace(temp, path)
    return payload


def set_paused(settings: Any, paused: bool, *, updated_by: str = "monitor") -> dict[str, Any]:
    value = read_control(settings)
    value["paused"] = bool(paused)
    value["updated_by"] = updated_by
    value["message"] = (
        "Automação totalmente pausada: sem consultas, varreduras, novos jobs ou abertura do Lightroom."
        if paused
        else "Automação ativa; checagem imediata solicitada."
    )
    if paused:
        _write_flag(
            pause_flag_path(settings),
            f"paused_at={datetime.now().isoformat(timespec='seconds')}\nupdated_by={updated_by}\n",
        )
    else:
        _remove_flag(pause_flag_path(settings))
    return write_control(settings, value)


def request_force_next(settings: Any, *, updated_by: str = "monitor") -> dict[str, Any]:
    value = read_control(settings)
    value["force_next_requested"] = True
    value["force_requested_at"] = datetime.now().isoformat(timespec="seconds")
    value["updated_by"] = updated_by
    value["message"] = "Próximo job solicitado; um único ciclo poderá rodar mesmo com a automação pausada."
    _write_flag(
        force_once_flag_path(settings),
        f"requested_at={value['force_requested_at']}\nupdated_by={updated_by}\n",
    )
    return write_control(settings, value)


def consume_force_next(settings: Any, *, message: str) -> dict[str, Any]:
    value = read_control(settings)
    value["force_next_requested"] = False
    value["force_consumed_at"] = datetime.now().isoformat(timespec="seconds")
    value["updated_by"] = "session-agent"
    value["message"] = message
    # O marcador force_once é consumido pelo plugin apenas quando ele realmente
    # inicia um job. Assim o bypass continua válido mesmo com a automação pausada.
    return write_control(settings, value)
