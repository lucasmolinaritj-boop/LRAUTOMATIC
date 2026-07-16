from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


INVALID_NAME_CHARS = '<>:"/\\|?*'


@dataclass(slots=True)
class CatalogCreationResult:
    catalog_path: Path
    launched: bool


def safe_catalog_name(name: str) -> str:
    cleaned = ''.join(ch for ch in name.strip() if ch not in INVALID_NAME_CHARS).rstrip('. ')
    if not cleaned:
        raise ValueError('Nome de catálogo inválido')
    return cleaned


def create_catalog(settings: Settings, name: str, *, open_lightroom: bool = True) -> CatalogCreationResult:
    """Cria um catálogo gerenciado pelo app a partir do modelo oficial do usuário.

    O modelo é necessário porque o Lightroom SDK não cria um .lrcat do zero. O usuário
    fornece uma vez um catálogo vazio criado pelo Lightroom; depois o app faz todo o
    restante sem interação manual.
    """
    if not settings.catalog_template or not settings.catalog_template.is_file():
        raise FileNotFoundError('Configure catalog_template apontando para um catálogo vazio válido')
    if not settings.catalog_output_root:
        raise ValueError('Configure catalog_output_root')

    safe_name = safe_catalog_name(name)
    destination_dir = settings.catalog_output_root / safe_name
    destination_dir.mkdir(parents=True, exist_ok=False)
    destination = destination_dir / f'{safe_name}.lrcat'
    shutil.copy2(settings.catalog_template, destination)

    launched = False
    if open_lightroom:
        executable = settings.lightroom_executable
        if not executable or not executable.is_file():
            shutil.rmtree(destination_dir, ignore_errors=True)
            raise FileNotFoundError('Configure lightroom_executable para abrir o catálogo automaticamente')
        subprocess.Popen([str(executable), str(destination)], cwd=str(executable.parent), close_fds=True)
        launched = True

    return CatalogCreationResult(catalog_path=destination, launched=launched)
