from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_EXTENSIONS = frozenset({".cr2", ".cr3", ".dng"})
PROGRESS_INTERVAL_SECONDS = 0.25
PROGRESS_EVERY_FILES = 100


@dataclass(frozen=True, slots=True)
class ResilientScanResult:
    counts: dict[str, int]
    latest_mtime: float | None
    zero_byte_count: int
    zero_byte_files: tuple[str, ...]
    errors: tuple[str, ...]
    timed_out: bool
    suspect_path: str | None
    elapsed_seconds: float
    folder_exists: bool

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def has_problem(self) -> bool:
        return self.timed_out or bool(self.errors) or self.zero_byte_count > 0


def _normalized_extensions(extensions: Iterable[str]) -> tuple[str, ...]:
    normalized = {
        f".{str(value).strip().lower().lstrip('.')}"
        for value in extensions
        if str(value).strip().lstrip('.')
    }
    return tuple(sorted(normalized))


def _scan_worker(root: str, extensions: tuple[str, ...], output: mp.Queue) -> None:
    keys = {suffix: suffix[1:] for suffix in extensions}
    counts = {key: 0 for key in keys.values()}
    latest_mtime: float | None = None
    zero_byte_count = 0
    zero_byte_files: list[str] = []
    errors: list[str] = []
    stack = [root]
    processed = 0
    last_progress = 0.0

    def progress(path: str, *, force: bool = False) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if force or processed % PROGRESS_EVERY_FILES == 0 or now - last_progress >= PROGRESS_INTERVAL_SECONDS:
            try:
                output.put_nowait(("progress", path))
            except queue.Full:
                pass
            last_progress = now

    try:
        try:
            root_is_dir = os.path.isdir(root)
        except OSError as exc:
            output.put(("result", False, counts, latest_mtime, zero_byte_count, zero_byte_files, [f"{root}: {exc}"]))
            return
        if not root_is_dir:
            output.put(("result", False, counts, latest_mtime, zero_byte_count, zero_byte_files, []))
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
                            stat = entry.stat(follow_symlinks=False)
                            counts[key] += 1
                            if stat.st_size == 0:
                                zero_byte_count += 1
                                if len(zero_byte_files) < 100:
                                    zero_byte_files.append(entry.path)
                            latest_mtime = stat.st_mtime if latest_mtime is None else max(latest_mtime, stat.st_mtime)
                        except OSError as exc:
                            if len(errors) < 100:
                                errors.append(f"{entry.path}: {exc}")
            except OSError as exc:
                if len(errors) < 100:
                    errors.append(f"{current}: {exc}")
        output.put(("result", True, counts, latest_mtime, zero_byte_count, zero_byte_files, errors))
    except BaseException as exc:
        try:
            output.put(("fatal", f"{root}: {type(exc).__name__}: {exc}"))
        except BaseException:
            pass


def scan_folder_resilient(
    root: str | Path,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
    *,
    timeout_seconds: float = 20.0,
    max_total_seconds: float = 300.0,
) -> ResilientScanResult:
    """Varre uma pasta em processo isolado com timeout por inatividade.

    O limite curto só dispara quando o processo deixa de informar progresso. Uma pasta
    grande e saudável pode continuar por até ``max_total_seconds``.
    """
    started = time.perf_counter()
    path = str(Path(root))
    normalized = _normalized_extensions(extensions)
    ctx = mp.get_context("spawn")
    output: mp.Queue = ctx.Queue(maxsize=256)
    process = ctx.Process(target=_scan_worker, args=(path, normalized, output), daemon=True)

    try:
        process.start()
    except BaseException as exc:
        return ResilientScanResult(
            counts={suffix[1:]: 0 for suffix in normalized},
            latest_mtime=None,
            zero_byte_count=0,
            zero_byte_files=(),
            errors=(f"Não foi possível iniciar o leitor isolado: {type(exc).__name__}: {exc}",),
            timed_out=False,
            suspect_path=path,
            elapsed_seconds=time.perf_counter() - started,
            folder_exists=False,
        )

    inactivity_limit = max(1.0, float(timeout_seconds))
    total_limit = max(inactivity_limit, float(max_total_seconds))
    last_progress = time.monotonic()
    absolute_deadline = last_progress + total_limit
    suspect_path: str | None = path
    result_payload = None
    fatal_error: str | None = None
    timed_out = False

    while time.monotonic() < absolute_deadline:
        remaining_inactivity = inactivity_limit - (time.monotonic() - last_progress)
        if remaining_inactivity <= 0:
            timed_out = True
            break
        try:
            message = output.get(timeout=max(0.05, min(0.25, remaining_inactivity)))
        except queue.Empty:
            if not process.is_alive():
                break
            continue
        kind = message[0]
        if kind == "progress":
            suspect_path = str(message[1])
            last_progress = time.monotonic()
        elif kind == "result":
            result_payload = message[1:]
            break
        elif kind == "fatal":
            fatal_error = str(message[1])
            break
    else:
        timed_out = True

    if process.is_alive():
        process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)
    try:
        output.close()
        output.join_thread()
    except (OSError, ValueError):
        pass

    if result_payload is not None:
        folder_exists, counts, latest_mtime, zero_byte_count, zero_byte_files, errors = result_payload
        return ResilientScanResult(
            counts=dict(counts),
            latest_mtime=latest_mtime,
            zero_byte_count=int(zero_byte_count),
            zero_byte_files=tuple(zero_byte_files),
            errors=tuple(errors),
            timed_out=False,
            suspect_path=None,
            elapsed_seconds=time.perf_counter() - started,
            folder_exists=bool(folder_exists),
        )

    if timed_out:
        error = f"Sem progresso por {inactivity_limit:.0f}s; possível bloqueio do Google Drive em {suspect_path}"
    else:
        error = fatal_error or f"Leitor isolado terminou sem resposta em {path}"
    return ResilientScanResult(
        counts={suffix[1:]: 0 for suffix in normalized},
        latest_mtime=None,
        zero_byte_count=0,
        zero_byte_files=(),
        errors=(error,),
        timed_out=timed_out,
        suspect_path=suspect_path,
        elapsed_seconds=time.perf_counter() - started,
        folder_exists=not timed_out,
    )