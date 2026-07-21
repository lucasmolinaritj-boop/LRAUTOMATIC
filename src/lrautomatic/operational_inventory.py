from __future__ import annotations

import json
import os
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import Settings
from .homepicz_scheduler import ImportWindow, current_import_window
from .resilient_scanner import DEFAULT_EXTENSIONS, scan_folder_resilient

RAW_EXTENSIONS = set(DEFAULT_EXTENSIONS)
MAX_WORKERS = 4
FOLDER_SCAN_TIMEOUT_SECONDS = 20.0
IGNORED_FOLDER_NAMES = frozenset({"video"})


def _normalized_folder_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


def _is_ignored_folder_name(value: object) -> bool:
    return _normalized_folder_name(value) in IGNORED_FOLDER_NAMES


def normalize_raw_extensions(extensions: Iterable[str] | None) -> frozenset[str]:
    if extensions is None:
        return frozenset(RAW_EXTENSIONS)
    normalized = {
        f".{str(value).strip().lower().lstrip('.')}"
        for value in extensions
        if str(value).strip().lstrip('.')
    }
    invalid = normalized - RAW_EXTENSIONS
    if invalid:
        raise ValueError(f"Extensão(ões) não permitida(s): {', '.join(sorted(invalid))}")
    if not normalized:
        raise ValueError("Selecione ao menos uma extensão RAW para excluir.")
    return frozenset(normalized)


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
    zero_byte_count: int = 0
    scan_timed_out: bool = False
    suspect_path: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.cr2 + self.cr3 + self.dng

    def count_for_extensions(self, extensions: Iterable[str] | None) -> int:
        selected = normalize_raw_extensions(extensions)
        counts = {".cr2": self.cr2, ".cr3": self.cr3, ".dng": self.dng}
        return sum(counts.get(extension, 0) for extension in selected)

    @property
    def has_scan_problem(self) -> bool:
        return self.scan_timed_out or self.zero_byte_count > 0 or bool(self.errors)

    @property
    def warning(self) -> str:
        if not self.folder_exists:
            return "⚠ Pasta não encontrada"
        if self.scan_timed_out and self.total == 0:
            return "⚠ Leitura parcial: uma subpasta travou no Drive"
        if self.zero_byte_count:
            return f"⚠ {self.zero_byte_count} arquivo(s) RAW com 0 byte"
        if self.errors:
            return "⚠ Leitura parcial — demais pastas preservadas"
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
        return sum(item.total == 0 and not item.has_scan_problem for item in self.folders)

    @property
    def missing_count(self) -> int:
        return sum(not item.folder_exists for item in self.folders)

    @property
    def problem_count(self) -> int:
        return sum(item.has_scan_problem for item in self.folders)

    @property
    def zero_byte_count(self) -> int:
        return sum(item.zero_byte_count for item in self.folders)

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
    extensions: tuple[str, ...] = ()


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
    works: list[dict[str, str]] = []
    if isinstance(raw_works, list):
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
            for value in ids if str(value).strip()
        ]
    unique = {item["id"]: item for item in works}
    return target_window, list(unique.values())


def _scan_work(root: Path, work: dict[str, str]) -> OperationalFolder:
    work_id = work["id"]
    folder = root / work_id

    try:
        folder_exists = folder.is_dir()
    except OSError:
        folder_exists = False

    if not folder_exists:
        return OperationalFolder(
            work_id=work_id,
            photographer=work.get("fotografo") or "Fotógrafo não informado",
            service=work.get("servico") or "",
            status=work.get("status") or "",
            scheduled_at=work.get("dataHora") or "",
            path=str(folder),
            folder_exists=False,
            cr2=0,
            cr3=0,
            dng=0,
            latest_mtime=None,
        )

    counts = {"cr2": 0, "cr3": 0, "dng": 0}
    latest_mtime: float | None = None
    zero_byte_count = 0
    timed_out = False
    suspect_path: str | None = None
    errors: list[str] = []
    scan_targets: list[Path] = []

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if _is_ignored_folder_name(entry.name):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        scan_targets.append(Path(entry.path))
                        continue
                    suffix = Path(entry.name).suffix.lower()
                    if suffix not in RAW_EXTENSIONS:
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    key = suffix.lstrip(".")
                    counts[key] += 1
                    if stat.st_size == 0:
                        zero_byte_count += 1
                        errors.append(f"Arquivo RAW com 0 byte: {entry.path}")
                    latest_mtime = stat.st_mtime if latest_mtime is None else max(latest_mtime, stat.st_mtime)
                except OSError as exc:
                    if Path(entry.name).suffix.lower() in RAW_EXTENSIONS:
                        errors.append(f"{entry.path}: {exc}")
    except OSError as exc:
        errors.append(f"Não foi possível listar a pasta do trabalho: {folder}: {exc}")

    for target in scan_targets:
        result = scan_folder_resilient(target, RAW_EXTENSIONS, timeout_seconds=FOLDER_SCAN_TIMEOUT_SECONDS)
        for key in counts:
            counts[key] += int(result.counts.get(key, 0))
        if result.latest_mtime is not None:
            latest_mtime = result.latest_mtime if latest_mtime is None else max(latest_mtime, result.latest_mtime)
        zero_byte_count += result.zero_byte_count
        errors.extend(result.errors)
        errors.extend(f"Arquivo RAW com 0 byte: {path}" for path in result.zero_byte_files[:20])
        if result.timed_out:
            timed_out = True
            suspect_path = result.suspect_path or str(target)
            errors.append(f"Subpasta ignorada após travar no Google Drive: {suspect_path}")

    return OperationalFolder(
        work_id=work_id,
        photographer=work.get("fotografo") or "Fotógrafo não informado",
        service=work.get("servico") or "",
        status=work.get("status") or "",
        scheduled_at=work.get("dataHora") or "",
        path=str(folder),
        folder_exists=True,
        cr2=counts["cr2"],
        cr3=counts["cr3"],
        dng=counts["dng"],
        latest_mtime=latest_mtime,
        zero_byte_count=zero_byte_count,
        scan_timed_out=timed_out,
        suspect_path=suspect_path,
        errors=tuple(errors[:50]),
    )


def scan_operational_inventory(settings: Settings, now: datetime | None = None) -> OperationalInventory:
    started = time.perf_counter()
    root = Path(settings.homepicz_photos_root).expanduser().resolve()
    window = current_import_window(settings, now)
    window, works = fetch_operational_works(settings, window)
    folders: list[OperationalFolder] = []
    errors: list[str] = []

    workers = max(1, min(MAX_WORKERS, len(works)))
    if works:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="operational-inventory") as executor:
            futures = {executor.submit(_scan_work, root, work): work["id"] for work in works}
            for future in as_completed(futures):
                work_id = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    errors.append(f"ID {work_id}: falha inesperada na contagem: {type(exc).__name__}: {exc}")
                    continue
                folders.append(item)
                errors.extend(f"ID {item.work_id}: {error}" for error in item.errors)

    folders.sort(key=lambda item: (item.scheduled_at, item.work_id.lower()))
    return OperationalInventory(
        root=str(root),
        window=window,
        folders=tuple(folders),
        elapsed_seconds=time.perf_counter() - started,
        errors=tuple(errors[:200]),
    )


def _delete_folder_raw_files(
    root: Path,
    folder: OperationalFolder,
    extensions: frozenset[str],
) -> RawDeletionFolderResult:
    candidate = Path(folder.path).resolve()
    deleted = failed = bytes_freed = 0
    errors: list[str] = []
    try:
        candidate.relative_to(root)
    except ValueError:
        return RawDeletionFolderResult(folder.work_id, folder.photographer, 0, 1, 0, (f"Caminho recusado fora da raiz: {candidate}",))
    try:
        if not candidate.is_dir():
            return RawDeletionFolderResult(folder.work_id, folder.photographer, 0, 0, 0, ())

        def on_walk_error(exc: OSError) -> None:
            nonlocal failed
            failed += 1
            if len(errors) < 50:
                errors.append(str(exc))

        for current, directories, filenames in os.walk(candidate, topdown=True, onerror=on_walk_error, followlinks=False):
            directories[:] = [name for name in directories if not _is_ignored_folder_name(name)]
            for filename in filenames:
                path = Path(current) / filename
                if path.suffix.lower() not in extensions:
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    deleted += 1
                    bytes_freed += size
                except OSError as exc:
                    failed += 1
                    if len(errors) < 50:
                        errors.append(f"{path}: {exc}")
    except OSError as exc:
        failed += 1
        errors.append(f"{candidate}: {exc}")
    return RawDeletionFolderResult(folder.work_id, folder.photographer, deleted, failed, bytes_freed, tuple(errors[:50]))


def delete_snapshot_raw_files(
    snapshot: OperationalInventory,
    work_ids: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
) -> RawDeletionResult:
    selected_extensions = normalize_raw_extensions(extensions)
    root = Path(snapshot.root).resolve()
    selected = snapshot.select(work_ids)
    results: list[RawDeletionFolderResult] = []
    workers = max(1, min(MAX_WORKERS, len(selected)))
    if selected:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raw-cleanup") as executor:
            futures = {
                executor.submit(_delete_folder_raw_files, root, folder, selected_extensions): folder
                for folder in selected
            }
            for future in as_completed(futures):
                folder = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(RawDeletionFolderResult(
                        folder.work_id,
                        folder.photographer,
                        0,
                        1,
                        0,
                        (f"Falha inesperada: {type(exc).__name__}: {exc}",),
                    ))
    results.sort(key=lambda item: (item.photographer.lower(), item.work_id.lower()))
    errors = [error for result in results for error in result.errors]
    return RawDeletionResult(
        deleted=sum(item.deleted for item in results),
        failed=sum(item.failed for item in results),
        bytes_freed=sum(item.bytes_freed for item in results),
        errors=tuple(errors[:100]),
        folders=tuple(results),
        extensions=tuple(sorted(selected_extensions)),
    )