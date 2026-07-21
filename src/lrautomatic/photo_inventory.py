from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

RAW_EXTENSIONS = {".cr2", ".cr3", ".dng"}


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


def _count_tree(root: Path) -> tuple[dict[str, int], list[str]]:
    counts = {"cr2": 0, "cr3": 0, "dng": 0}
    errors: list[str] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            suffix = Path(entry.name).suffix.lower()
                            if suffix in RAW_EXTENSIONS:
                                counts[suffix[1:]] += 1
                    except OSError as exc:
                        errors.append(f"{entry.path}: {exc}")
        except OSError as exc:
            errors.append(f"{folder}: {exc}")
    return counts, errors


def scan_photo_inventory(root: Path) -> PhotoInventory:
    started = time.perf_counter()
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"Pasta Fotos do dia não encontrada: {root}")

    folders: list[FolderInventory] = []
    errors: list[str] = []
    root_counts = {"cr2": 0, "cr3": 0, "dng": 0}

    try:
        with os.scandir(root) as entries:
            top_entries = list(entries)
    except OSError as exc:
        raise OSError(f"Não foi possível ler {root}: {exc}") from exc

    top_entries.sort(key=lambda item: item.name.lower())
    loose_counts = {"cr2": 0, "cr3": 0, "dng": 0}
    for entry in top_entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                counts, found_errors = _count_tree(Path(entry.path))
                errors.extend(found_errors)
                folders.append(
                    FolderInventory(
                        name=entry.name,
                        path=entry.path,
                        cr2=counts["cr2"],
                        cr3=counts["cr3"],
                        dng=counts["dng"],
                    )
                )
                for key in root_counts:
                    root_counts[key] += counts[key]
            elif entry.is_file(follow_symlinks=False):
                suffix = Path(entry.name).suffix.lower()
                if suffix in RAW_EXTENSIONS:
                    loose_counts[suffix[1:]] += 1
                    root_counts[suffix[1:]] += 1
        except OSError as exc:
            errors.append(f"{entry.path}: {exc}")

    if sum(loose_counts.values()):
        folders.insert(
            0,
            FolderInventory(
                name="(arquivos na raiz)",
                path=str(root),
                cr2=loose_counts["cr2"],
                cr3=loose_counts["cr3"],
                dng=loose_counts["dng"],
            ),
        )

    folders.sort(key=lambda item: (-item.total, item.name.lower()))
    return PhotoInventory(
        root=str(root),
        cr2=root_counts["cr2"],
        cr3=root_counts["cr3"],
        dng=root_counts["dng"],
        folders=tuple(folders),
        elapsed_seconds=time.perf_counter() - started,
        errors=tuple(errors[:100]),
    )
