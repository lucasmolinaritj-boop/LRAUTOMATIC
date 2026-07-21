from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAW_EXTENSIONS = {".cr2", ".cr3", ".dng"}
CACHE_VERSION = 2
MAX_WORKERS = 8


@dataclass(frozen=True, slots=True)
class FolderInventory:
    name: str
    path: str
    cr2: int
    cr3: int
    dng: int

    @property
    def total(self) -> int:
        return self.cr2 + self.cr3 + self.dng


@dataclass(frozen=True, slots=True)
class PhotoInventory:
    root: str
    cr2: int
    cr3: int
    dng: int
    folders: tuple[FolderInventory, ...]
    elapsed_seconds: float
    errors: tuple[str, ...]

    @property
    def total(self) -> int:
        return self.cr2 + self.cr3 + self.dng


def _empty_counts() -> dict[str, int]:
    return {"cr2": 0, "cr3": 0, "dng": 0}


def _cache_path() -> Path:
    base = Path(os.path.expandvars(r"%LOCALAPPDATA%\LRAutomatic\control"))
    return base / "photo_inventory_cache_v2.json"


def _load_cache(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {"version": CACHE_VERSION, "root": str(root), "directories": {}}
    if not isinstance(payload, dict):
        return {"version": CACHE_VERSION, "root": str(root), "directories": {}}
    if payload.get("version") != CACHE_VERSION or payload.get("root") != str(root):
        return {"version": CACHE_VERSION, "root": str(root), "directories": {}}
    if not isinstance(payload.get("directories"), dict):
        payload["directories"] = {}
    return payload


def _save_cache(root: Path, directories: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "root": str(root),
        "updated_at": time.time(),
        "directories": directories,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(encoded, encoding="utf-8")
    os.replace(temp, path)


def _suffix_key(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".cr2"):
        return "cr2"
    if lower.endswith(".cr3"):
        return "cr3"
    if lower.endswith(".dng"):
        return "dng"
    return None


def _scan_directory(
    path: str,
    old_cache: dict[str, Any],
    new_cache: dict[str, Any],
    errors: list[str],
) -> dict[str, int]:
    """Conta uma árvore reutilizando diretórios inalterados.

    Mesmo quando um diretório pai não mudou, os filhos conhecidos são conferidos
    por mtime. Assim uma alteração em ``ID/internas`` é percebida sem enumerar de
    novo milhares de arquivos das demais pastas.
    """
    try:
        stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return _empty_counts()

    old = old_cache.get(path)
    unchanged = isinstance(old, dict) and old.get("mtime_ns") == stat.st_mtime_ns

    if unchanged:
        direct = old.get("direct") if isinstance(old.get("direct"), dict) else _empty_counts()
        children = old.get("children") if isinstance(old.get("children"), list) else []
        total = {key: int(direct.get(key, 0)) for key in ("cr2", "cr3", "dng")}
        valid_children: list[str] = []
        for child in children:
            if not isinstance(child, str):
                continue
            child_counts = _scan_directory(child, old_cache, new_cache, errors)
            if child in new_cache:
                valid_children.append(child)
            for key in total:
                total[key] += child_counts[key]
        new_cache[path] = {
            "mtime_ns": stat.st_mtime_ns,
            "direct": direct,
            "children": valid_children,
            "total": total,
        }
        return total

    direct = _empty_counts()
    children: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        children.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        key = _suffix_key(entry.name)
                        if key:
                            direct[key] += 1
                except OSError as exc:
                    errors.append(f"{entry.path}: {exc}")
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return _empty_counts()

    total = dict(direct)
    for child in children:
        child_counts = _scan_directory(child, old_cache, new_cache, errors)
        for key in total:
            total[key] += child_counts[key]

    new_cache[path] = {
        "mtime_ns": stat.st_mtime_ns,
        "direct": direct,
        "children": children,
        "total": total,
    }
    return total


def _scan_top_folder(entry: os.DirEntry[str], old_cache: dict[str, Any]) -> tuple[FolderInventory, dict[str, Any], list[str]]:
    local_cache: dict[str, Any] = {}
    errors: list[str] = []
    counts = _scan_directory(entry.path, old_cache, local_cache, errors)
    return (
        FolderInventory(
            name=entry.name,
            path=entry.path,
            cr2=counts["cr2"],
            cr3=counts["cr3"],
            dng=counts["dng"],
        ),
        local_cache,
        errors,
    )


def scan_photo_inventory(root: Path) -> PhotoInventory:
    started = time.perf_counter()
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Pasta Fotos do dia não encontrada: {root}")

    cache = _load_cache(root)
    old_cache: dict[str, Any] = cache.get("directories", {})
    new_cache: dict[str, Any] = {}
    folders: list[FolderInventory] = []
    errors: list[str] = []
    root_counts = _empty_counts()
    loose_counts = _empty_counts()

    try:
        with os.scandir(root) as entries:
            top_entries = list(entries)
    except OSError as exc:
        raise OSError(f"Não foi possível ler {root}: {exc}") from exc

    directory_entries: list[os.DirEntry[str]] = []
    for entry in top_entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                directory_entries.append(entry)
            elif entry.is_file(follow_symlinks=False):
                key = _suffix_key(entry.name)
                if key:
                    loose_counts[key] += 1
                    root_counts[key] += 1
        except OSError as exc:
            errors.append(f"{entry.path}: {exc}")

    workers = max(1, min(MAX_WORKERS, len(directory_entries)))
    if directory_entries:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="photo-inventory") as executor:
            futures = [executor.submit(_scan_top_folder, entry, old_cache) for entry in directory_entries]
            for future in as_completed(futures):
                try:
                    folder, folder_cache, folder_errors = future.result()
                except Exception as exc:  # proteção para um compartilhamento/pasta defeituosa
                    errors.append(f"Falha inesperada na contagem: {exc}")
                    continue
                folders.append(folder)
                new_cache.update(folder_cache)
                errors.extend(folder_errors)
                root_counts["cr2"] += folder.cr2
                root_counts["cr3"] += folder.cr3
                root_counts["dng"] += folder.dng

    if sum(loose_counts.values()):
        folders.append(
            FolderInventory(
                name="(arquivos na raiz)",
                path=str(root),
                cr2=loose_counts["cr2"],
                cr3=loose_counts["cr3"],
                dng=loose_counts["dng"],
            )
        )

    folders.sort(key=lambda item: (-item.total, item.name.lower()))
    try:
        _save_cache(root, new_cache)
    except OSError as exc:
        errors.append(f"Cache do inventário: {exc}")

    return PhotoInventory(
        root=str(root),
        cr2=root_counts["cr2"],
        cr3=root_counts["cr3"],
        dng=root_counts["dng"],
        folders=tuple(folders),
        elapsed_seconds=time.perf_counter() - started,
        errors=tuple(errors[:100]),
    )
