from __future__ import annotations

import json
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

PREFERENCES_FILE = "editor_preferences.json"
DEFAULT_PREFERENCES = {
    "editor_name": "",
    "manager_scope": "all",
    "automation_scope": "all",
}
_WORK_METADATA: dict[str, dict[str, str]] = {}


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


def preferences_path(settings) -> Path:
    return Path(settings.control_dir) / PREFERENCES_FILE


def load_editor_preferences(settings) -> dict[str, str]:
    result = dict(DEFAULT_PREFERENCES)
    path = preferences_path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            result.update({key: str(raw.get(key, result[key]) or "").strip() for key in result})
    except (OSError, ValueError, TypeError):
        pass
    if result["manager_scope"] not in {"mine", "all"}:
        result["manager_scope"] = "all"
    if result["automation_scope"] not in {"mine", "all"}:
        result["automation_scope"] = "all"
    return result


def save_editor_preferences(settings, values: dict[str, object]) -> Path:
    current = load_editor_preferences(settings)
    for key in current:
        if key in values:
            current[key] = str(values[key] or "").strip()
    if current["manager_scope"] not in {"mine", "all"}:
        current["manager_scope"] = "all"
    if current["automation_scope"] not in {"mine", "all"}:
        current["automation_scope"] = "all"
    path = preferences_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def _query_for_window(window) -> str:
    params = (
        {"data": window.start.isoformat(), "detalhes": "1"}
        if window.start == window.end
        else {"inicio": window.start.isoformat(), "fim": window.end.isoformat(), "detalhes": "1"}
    )
    return urllib.parse.urlencode(params)


def fetch_editor_metadata(settings, window) -> dict[str, dict[str, str]]:
    if not settings.homepicz_appscript_url:
        return {}
    separator = "&" if "?" in settings.homepicz_appscript_url else "?"
    url = f"{settings.homepicz_appscript_url}{separator}{_query_for_window(window)}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "LRAutomatic/editor-features"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    records = payload.get("trabalhos", []) if isinstance(payload, dict) else []
    result: dict[str, dict[str, str]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            continue
        work_id = str(raw.get("id") or "").strip()
        if not work_id:
            continue
        result[work_id] = {
            "id": work_id,
            "editorFoto": str(raw.get("editorFoto") or "Editor de foto não informado").strip(),
            "cliente": str(raw.get("cliente") or "Cliente não informado").strip(),
            "rua": str(raw.get("endereco") or raw.get("rua") or "Rua não informada").strip(),
            "horario": str(raw.get("horario") or "").strip(),
            "dataHora": str(raw.get("dataHora") or "").strip(),
            "fotografo": str(raw.get("fotografo") or "Fotógrafo não informado").strip(),
            "servico": str(raw.get("servico") or "").strip(),
            "status": str(raw.get("status") or "").strip(),
        }
    return result


def filter_work_dicts(settings, works: list[dict[str, str]], scope_key: str) -> list[dict[str, str]]:
    prefs = load_editor_preferences(settings)
    if prefs.get(scope_key) != "mine":
        return works
    editor = _normalize(prefs.get("editor_name"))
    if not editor:
        return []
    return [work for work in works if _normalize(work.get("editorFoto")) == editor]


def install_homepicz_editor_features() -> None:
    try:
        from . import homepicz_scheduler as scheduler
    except Exception:
        return

    original_fetch = getattr(scheduler, "_fetch_work_items", None)
    if original_fetch is not None and not getattr(original_fetch, "_editor_filtered", False):
        def filtered_fetch(settings, window):
            items = original_fetch(settings, window)
            try:
                metadata = fetch_editor_metadata(settings, window)
            except Exception:
                metadata = {}
            _WORK_METADATA.clear()
            _WORK_METADATA.update(metadata)
            prefs = load_editor_preferences(settings)
            if prefs.get("automation_scope") != "mine":
                return items
            editor = _normalize(prefs.get("editor_name"))
            if not editor:
                return []
            return [item for item in items if _normalize(metadata.get(item.work_id, {}).get("editorFoto")) == editor]

        filtered_fetch._editor_filtered = True
        scheduler._fetch_work_items = filtered_fetch

    original_collection_name = getattr(scheduler, "_collection_name", None)
    if original_collection_name is not None and not getattr(original_collection_name, "_address_enabled", False):
        def collection_name(item):
            metadata = _WORK_METADATA.get(str(item.work_id), {})
            address = " ".join(str(metadata.get("rua") or "").split()).strip(" -")
            work_id = scheduler._clean_name(item.work_id, "Sem ID")
            return f"{work_id} - {address}" if address else work_id

        collection_name._address_enabled = True
        scheduler._collection_name = collection_name
