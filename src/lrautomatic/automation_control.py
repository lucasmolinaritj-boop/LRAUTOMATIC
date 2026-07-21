from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

CONTROL_FILENAME = "automation_control.json"


def control_path(settings: Any) -> Path:
    return settings.control_dir / CONTROL_FILENAME


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
        return _default()
    if not isinstance(value, dict):
        return _default()
    result = _default()
    result.update(value)
    result["paused"] = bool(result.get("paused"))
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
        "Automação pausada; a tarefa em andamento poderá terminar normalmente."
        if paused
        else "Automação ativa"
    )
    return write_control(settings, value)


def request_force_next(settings: Any, *, updated_by: str = "monitor") -> dict[str, Any]:
    value = read_control(settings)
    value["force_next_requested"] = True
    value["force_requested_at"] = datetime.now().isoformat(timespec="seconds")
    value["updated_by"] = updated_by
    value["message"] = "Próximo job solicitado; aguardando a tarefa atual terminar."
    return write_control(settings, value)


def consume_force_next(settings: Any, *, message: str) -> dict[str, Any]:
    value = read_control(settings)
    value["force_next_requested"] = False
    value["force_consumed_at"] = datetime.now().isoformat(timespec="seconds")
    value["updated_by"] = "session-agent"
    value["message"] = message
    return write_control(settings, value)
