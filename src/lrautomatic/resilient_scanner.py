from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_EXTENSIONS = frozenset({".cr2", ".cr3", ".dng"})


@dataclass(frozen=True, slots=True)
class ResilientScanResult:
    counts: dict[str, int]
    latest_mtime: float | None
    zero_byte_files: tuple[str, ...]
    errors: tuple[str, ...]
    timed_out: bool
    suspect_path: str | None
    elapsed_seconds: float

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def has_problem(self) -> bool:
        return self.timed_out or bool(self.errors) or bool(self.zero_byte_files)


def _scan_worker(root: str, extensions: tuple[str, ...], output: mp.Queue) -> None:
    counts = {suffix[1:]: 0 for suffix in extensions}
    latest_mtime: float | None = None
    zero_byte_files: list[str] = []
    errors: list[str] = []
    stack = [root]

    try:
        while stack:
            current = stack.pop()
            output.put(("progress", current))
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        output.put(("progress", entry.path))
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            suffix = Path(entry.name).suffix.lower()
                            if suffix not in counts:
                                continue
                            stat = entry.stat(follow_symlinks=False)
                            counts[suffix[1:]] += 1
                            if stat.st_size == 0:
                                zero_byte_files.append(entry.path)
                            latest_mtime = stat.st_mtime if latest_mtime is None else max(latest_mtime, stat.st_mtime)
                        except OSError as exc:
                            errors.append(f"{entry.path}: {exc}")
            except OSError as exc:
                errors.append(f"{current}: {exc}")
        output.put(("result", counts, latest_mtime, zero_byte_files[:100], errors[:100]))
    except BaseException as exc:
        output.put(("fatal", f"{root}: {type(exc).__name__}: {exc}"))


def scan_folder_resilient(
    root: str | Path,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
    *,
    timeout_seconds: float = 20.0,
) -> ResilientScanResult:
    """Varre uma pasta em processo isolado.

    Se Google Drive ou o sistema operacional bloquear uma chamada de arquivo, apenas
    o processo auxiliar é encerrado. A interface e o executor continuam vivos, e o
    último caminho tocado é devolvido como suspeito para diagnóstico.
    """
    started = time.perf_counter()
    path = str(Path(root))
    normalized = tuple(sorted({str(value).lower() for value in extensions}))
    ctx = mp.get_context("spawn")
    output: mp.Queue = ctx.Queue()
    process = ctx.Process(target=_scan_worker, args=(path, normalized, output), daemon=True)
    process.start()

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    suspect_path: str | None = path
    result_payload = None
    fatal_error: str | None = None

    while time.monotonic() < deadline:
        remaining = max(0.05, min(0.25, deadline - time.monotonic()))
        try:
            message = output.get(timeout=remaining)
        except queue.Empty:
            if not process.is_alive():
                break
            continue
        kind = message[0]
        if kind == "progress":
            suspect_path = str(message[1])
        elif kind == "result":
            result_payload = message[1:]
            break
        elif kind == "fatal":
            fatal_error = str(message[1])
            break

    timed_out = result_payload is None and fatal_error is None and process.is_alive()
    if process.is_alive():
        process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)
    output.close()

    if result_payload is not None:
        counts, latest_mtime, zero_byte_files, errors = result_payload
        return ResilientScanResult(
            counts=dict(counts),
            latest_mtime=latest_mtime,
            zero_byte_files=tuple(zero_byte_files),
            errors=tuple(errors),
            timed_out=False,
            suspect_path=None,
            elapsed_seconds=time.perf_counter() - started,
        )

    if timed_out:
        error = f"Tempo limite de {timeout_seconds:.0f}s excedido; possível bloqueio do Google Drive em {suspect_path}"
    else:
        error = fatal_error or f"Leitor isolado terminou sem resposta em {path}"
    return ResilientScanResult(
        counts={suffix[1:]: 0 for suffix in normalized},
        latest_mtime=None,
        zero_byte_files=(),
        errors=(error,),
        timed_out=timed_out,
        suspect_path=suspect_path,
        elapsed_seconds=time.perf_counter() - started,
    )
