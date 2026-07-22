from __future__ import annotations

import os
import queue
import time
from pathlib import Path
from typing import Any

from . import operational_inventory as inventory
from . import resilient_scanner as scanner


def _created_timestamp(stat_result: os.stat_result) -> float:
    """Retorna a criação real do arquivo no Windows.

    Python 3.12 pode expor ``st_birthtime``. Em versões/volumes onde ele não
    estiver disponível, ``st_ctime`` no Windows representa o horário de criação.
    """
    birth_time = getattr(stat_result, "st_birthtime", None)
    if birth_time is not None:
        return float(birth_time)
    return float(stat_result.st_ctime)


def _scan_worker_created(root: str, extensions: tuple[str, ...], output: Any) -> None:
    """Scanner isolado que calcula o último RAW pela criação, não modificação."""
    keys = {suffix: suffix[1:] for suffix in extensions}
    counts = {key: 0 for key in keys.values()}
    latest_created: float | None = None
    zero_byte_count = 0
    zero_byte_files: list[str] = []
    errors: list[str] = []
    stack = [root]
    processed = 0
    last_progress = 0.0

    def progress(path: str, *, force: bool = False) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if (
            force
            or processed % scanner.PROGRESS_EVERY_FILES == 0
            or now - last_progress >= scanner.PROGRESS_INTERVAL_SECONDS
        ):
            try:
                output.put_nowait(("progress", path))
            except queue.Full:
                pass
            last_progress = now

    try:
        try:
            root_is_dir = os.path.isdir(root)
        except OSError as exc:
            output.put(("result", False, counts, latest_created, zero_byte_count, zero_byte_files, [f"{root}: {exc}"]))
            return
        if not root_is_dir:
            output.put(("result", False, counts, latest_created, zero_byte_count, zero_byte_files, []))
            return

        while stack:
            current = stack.pop()
            progress(current, force=True)
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        processed += 1
                        progress(entry.path)
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            suffix = Path(entry.name).suffix.lower()
                            key = keys.get(suffix)
                            if key is None:
                                continue
                            stat_result = entry.stat(follow_symlinks=False)
                            counts[key] += 1
                            if stat_result.st_size == 0:
                                zero_byte_count += 1
                                if len(zero_byte_files) < 100:
                                    zero_byte_files.append(entry.path)
                            created = _created_timestamp(stat_result)
                            latest_created = created if latest_created is None else max(latest_created, created)
                        except OSError as exc:
                            if len(errors) < 100:
                                errors.append(f"{entry.path}: {exc}")
            except OSError as exc:
                if len(errors) < 100:
                    errors.append(f"{current}: {exc}")
        output.put(("result", True, counts, latest_created, zero_byte_count, zero_byte_files, errors))
    except BaseException as exc:
        try:
            output.put(("fatal", f"{root}: {type(exc).__name__}: {exc}"))
        except BaseException:
            pass


def _scan_work_created(root: Path, work: dict[str, str]) -> inventory.OperationalFolder:
    """Conta um trabalho e registra a criação mais recente entre todos os RAWs."""
    work_id = work["id"]
    folder = root / work_id

    try:
        folder_exists = folder.is_dir()
    except OSError:
        folder_exists = False

    if not folder_exists:
        return inventory.OperationalFolder(
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
    latest_created: float | None = None
    zero_byte_count = 0
    timed_out = False
    suspect_path: str | None = None
    errors: list[str] = []
    scan_targets: list[Path] = []

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if inventory._is_ignored_folder_name(entry.name):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        scan_targets.append(Path(entry.path))
                        continue
                    suffix = Path(entry.name).suffix.lower()
                    if suffix not in inventory.RAW_EXTENSIONS:
                        continue
                    stat_result = entry.stat(follow_symlinks=False)
                    key = suffix.lstrip(".")
                    counts[key] += 1
                    if stat_result.st_size == 0:
                        zero_byte_count += 1
                        errors.append(f"Arquivo RAW com 0 byte: {entry.path}")
                    created = _created_timestamp(stat_result)
                    latest_created = created if latest_created is None else max(latest_created, created)
                except OSError as exc:
                    if Path(entry.name).suffix.lower() in inventory.RAW_EXTENSIONS:
                        errors.append(f"{entry.path}: {exc}")
    except OSError as exc:
        errors.append(f"Não foi possível listar a pasta do trabalho: {folder}: {exc}")

    for target in scan_targets:
        result = inventory.scan_folder_resilient(
            target,
            inventory.RAW_EXTENSIONS,
            timeout_seconds=inventory.FOLDER_SCAN_TIMEOUT_SECONDS,
        )
        for key in counts:
            counts[key] += int(result.counts.get(key, 0))
        # latest_mtime é mantido por compatibilidade, mas agora carrega criação.
        if result.latest_mtime is not None:
            latest_created = (
                result.latest_mtime
                if latest_created is None
                else max(latest_created, result.latest_mtime)
            )
        zero_byte_count += result.zero_byte_count
        errors.extend(result.errors)
        errors.extend(f"Arquivo RAW com 0 byte: {path}" for path in result.zero_byte_files[:20])
        if result.timed_out:
            timed_out = True
            suspect_path = result.suspect_path or str(target)
            errors.append(f"Subpasta ignorada após travar no Google Drive: {suspect_path}")

    return inventory.OperationalFolder(
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
        latest_mtime=latest_created,
        zero_byte_count=zero_byte_count,
        scan_timed_out=timed_out,
        suspect_path=suspect_path,
        errors=tuple(errors[:50]),
    )


def install_raw_creation_time() -> None:
    """Ativa o cálculo por criação em pastas diretas e subpastas isoladas."""
    if not getattr(scanner._scan_worker, "_uses_creation_time", False):
        _scan_worker_created._uses_creation_time = True
        scanner._scan_worker = _scan_worker_created

    if not getattr(inventory._scan_work, "_uses_creation_time", False):
        _scan_work_created._uses_creation_time = True
        inventory._scan_work = _scan_work_created
