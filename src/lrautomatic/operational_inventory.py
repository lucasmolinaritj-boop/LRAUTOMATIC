from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .homepicz_scheduler import ImportWindow, current_import_window

RAW_EXTENSIONS = {".cr2", ".cr3", ".dng"}
MAX_WORKERS = 8


@dataclass(frozen=True, slots=True)
class OperationalFolder:
    work_id: str
    photographer: str
    service: str
    status: str
    scheduled_at: str
    path: str
    folder_exists: bool
    cr2: int
    cr3: int
    dng: int
    latest_mtime: float | None
    errors: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.cr2 + self.cr3 + self.dng

    @property
    def warning(self) -> str:
        if not self.folder_exists:
            return "⚠ Pasta não encontrada"
        if self.total == 0:
            return "⚠ Sem arquivos RAW"
        return "OK"


@dataclass(frozen=True, slots=True)
class OperationalInventory:
    root: str
    window: ImportWindow
    folders: tuple[OperationalFolder, ...]
    elapsed_seconds: float
    errors: tuple[str, ...]

    @property
    def cr2(self) -> int:
        return sum(item.cr2 for item in self.folders)

    @property
    def cr3(self) -> int:
        return sum(item.cr3 for item in self.folders)

    @property
    def dng(self) -> int:
        return sum(item.dng for item in self.folders)

    @property
    def total(self) -> int:
        return self.cr2 + self.cr3 + self.dng

    @property
    def empty_count(self) -> int:
        return sum(item.total == 0 for item in self.folders)

    @property
    def missing_count(self) -> int:
        return sum(not item.folder_exists for item in self.folders)

    def select(self, work_ids: Iterable[str] | None = None) -> tuple[OperationalFolder, ...]:
        if work_ids is None:
            return self.folders
        wanted = {str(value).strip() for value in work_ids if str(value).strip()}
        return tuple(item for item in self.folders if item.work_id in wanted)


@dataclass(frozen=True, slots=True)
class RawDeletionFolderResult:
    work_id: str
    photographer: str
    deleted: int
    failed: int
    bytes_freed: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RawDeletionResult:
    deleted: int
    failed: int
    bytes_freed: int
    errors: tuple[str, ...]
    folders: tuple[RawDeletionFolderResult, ...] = ()


def _query_for_window(window: ImportWindow) -> str:
    if window.start == window.end:
        return urllib.parse.urlencode({"data": window.start.isoformat()})
    return urllib.parse.urlencode({"inicio": window.start.isoformat(), "fim": window.end.isoformat()})


def fetch_operational_works(settings: Settings, window: ImportWindow | None = None) -> tuple[ImportWindow, list[dict[str, str]]]:
    if not settings.homepicz_appscript_url:
        raise RuntimeError("Configure a URL do Google Apps Script nas Configurações.")

    target_window = window or current_import_window(settings)
    separator = "&" if "?" in settings.homepicz_appscript_url else "?"
    url = f"{settings.homepicz_appscript_url}{separator}{_query_for_window(target_window)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "LRAutomatic/operational-inventory"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))

    if not isinstance(payload, dict):
        raise RuntimeError("Apps Script devolveu uma resposta inválida.")
    if payload.get("error"):
        raise RuntimeError(f"Apps Script: {payload['error']}")

    raw_works = payload.get("trabalhos")
    if isinstance(raw_works, list):
        works = []
        for raw in raw_works:
            if not isinstance(raw, dict):
                continue
            work_id = str(raw.get("id") or "").strip()
            if not work_id:
                continue
            works.append({
                "id": work_id,
                "fotografo": str(raw.get("fotografo") or "Fotógrafo não informado").strip(),
                "servico": str(raw.get("servico") or "").strip(),
                "status": str(raw.get("status") or "").strip(),
                "dataHora": str(raw.get("dataHora") or "").strip(),
            })
    else:
        ids = payload.get("ids")
        if not isinstance(ids, list):
            raise RuntimeError("Apps Script respondeu sem os campos trabalhos ou ids.")
        works = [
            {"id": str(value).strip(), "fotografo": "Fotógrafo não informado", "servico": "", "status": "", "dataHora": ""}
            for value in ids
            if str(value).strip()
        ]

    unique = {item["id"]: item for item in works}
    return target_window, list(unique.values())


def _scan_work(root: Path, work: dict[str, str]) -> OperationalFolder:
    work_id = work["id"]
    folder = root / work_id
    counts = {"cr2": 0, "cr3": 0, "dng": 0}
    latest_mtime: float | None = None
    errors: list[str] = []

    if folder.is_dir():
        stack = [folder]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                suffix = Path(entry.name).suffix.lower()
                                if suffix in RAW_EXTENSIONS:
                                    counts[suffix[1:]] += 1
                                    try:
                                        modified = entry.stat(follow_symlinks=False).st_mtime
                                        latest_mtime = modified if latest_mtime is None else max(latest_mtime, modified)
                                    except OSError:
                                        pass
                        except OSError as exc:
                            errors.append(f"{entry.path}: {exc}")
            except OSError as exc:
                errors.append(f"{current}: {exc}")

    return OperationalFolder(
        work_id=work_id,
        photographer=work.get("fotografo") or "Fotógrafo não informado",
        service=work.get("servico") or "",
        status=work.get("status") or "",
        scheduled_at=work.get("dataHora") or "",
        path=str(folder),
        folder_exists=folder.is_dir(),
        cr2=counts["cr2"],
        cr3=counts["cr3"],
        dng=counts["dng"],
        latest_mtime=latest_mtime,
        errors=tuple(errors[:20]),
    )


def scan_operational_inventory(settings: Settings, now: datetime | None = None) -> OperationalInventory:
    started = time.perf_counter()
    root = Path(settings.homepicz_photos_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Pasta Fotos do dia não encontrada: {root}")

    window = current_import_window(settings, now)
    window, works = fetch_operational_works(settings, window)
    folders: list[OperationalFolder] = []
    errors: list[str] = []

    workers = max(1, min(MAX_WORKERS, len(works)))
    if works:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="operational-inventory") as executor:
            futures = [executor.submit(_scan_work, root, work) for work in works]
            for future in as_completed(futures):
                try:
                    item = future.result()
                except Exception as exc:
                    errors.append(f"Falha inesperada na contagem: {exc}")
                    continue
                folders.append(item)
                errors.extend(item.errors)

    folders.sort(key=lambda item: (item.scheduled_at, item.work_id.lower()))
    return OperationalInventory(
        root=str(root),
        window=window,
        folders=tuple(folders),
        elapsed_seconds=time.perf_counter() - started,
        errors=tuple(errors[:100]),
    )


def _delete_folder_raw_files(root: Path, folder: OperationalFolder) -> RawDeletionFolderResult:
    candidate = Path(folder.path).resolve()
    deleted = failed = bytes_freed = 0
    errors: list[str] = []

    try:
        candidate.relative_to(root)
    except ValueError:
        return RawDeletionFolderResult(
            work_id=folder.work_id,
            photographer=folder.photographer,
            deleted=0,
            failed=1,
            bytes_freed=0,
            errors=(f"Caminho recusado fora da raiz: {candidate}",),
        )

    if not candidate.is_dir():
        return RawDeletionFolderResult(folder.work_id, folder.photographer, 0, 0, 0, ())

    try:
        paths = list(candidate.rglob("*"))
    except OSError as exc:
        return RawDeletionFolderResult(folder.work_id, folder.photographer, 0, 1, 0, (f"{candidate}: {exc}",))

    for path in paths:
        if path.suffix.lower() not in RAW_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            deleted += 1
            bytes_freed += size
        except OSError as exc:
            failed += 1
            errors.append(f"{path}: {exc}")

    return RawDeletionFolderResult(
        work_id=folder.work_id,
        photographer=folder.photographer,
        deleted=deleted,
        failed=failed,
        bytes_freed=bytes_freed,
        errors=tuple(errors[:50]),
    )


def delete_snapshot_raw_files(
    snapshot: OperationalInventory,
    work_ids: Iterable[str] | None = None,
) -> RawDeletionResult:
    """Exclui CR2/CR3/DNG somente das pastas escolhidas do snapshot confirmado."""
    root = Path(snapshot.root).resolve()
    selected = snapshot.select(work_ids)
    results: list[RawDeletionFolderResult] = []

    workers = max(1, min(MAX_WORKERS, len(selected)))
    if selected:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raw-cleanup") as executor:
            futures = [executor.submit(_delete_folder_raw_files, root, folder) for folder in selected]
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda item: (item.photographer.lower(), item.work_id.lower()))
    errors = [error for result in results for error in result.errors]
    return RawDeletionResult(
        deleted=sum(item.deleted for item in results),
        failed=sum(item.failed for item in results),
        bytes_freed=sum(item.bytes_freed for item in results),
        errors=tuple(errors[:100]),
        folders=tuple(results),
    )
