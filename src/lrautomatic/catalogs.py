from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import Settings

INVALID_NAME_CHARS = '<>:"/\\|?*'


@dataclass(slots=True)
class CatalogCreationResult:
    catalog_path: Path
    catalog_dir: Path
    manifest_path: Path
    launched: bool


def safe_catalog_name(name: str) -> str:
    cleaned = ''.join(ch for ch in name.strip() if ch not in INVALID_NAME_CHARS).rstrip('. ')
    if not cleaned:
        raise ValueError('Nome de catálogo inválido')
    return cleaned


def _validate_template(template: Path) -> None:
    if template.suffix.lower() != '.lrcat':
        raise ValueError('catalog_template precisa apontar para um arquivo .lrcat')
    if template.stat().st_size < 4096:
        raise ValueError('O catálogo-modelo parece vazio, incompleto ou corrompido')


def create_catalog(settings: Settings, name: str, *, open_lightroom: bool = True) -> CatalogCreationResult:
    """Cria um catálogo gerenciado, de forma atômica, a partir de um .lrcat oficial vazio."""
    template = settings.catalog_template
    root = settings.catalog_output_root
    if not template or not template.is_file():
        raise FileNotFoundError('Configure catalog_template apontando para um catálogo vazio válido')
    if not root:
        raise ValueError('Configure catalog_output_root')
    _validate_template(template)

    safe_name = safe_catalog_name(name)
    root.mkdir(parents=True, exist_ok=True)
    destination_dir = root / safe_name
    if destination_dir.exists():
        raise FileExistsError(f'Já existe um catálogo chamado {safe_name}: {destination_dir}')

    staging = root / f'.{safe_name}.creating-{uuid4().hex[:8]}'
    destination = staging / f'{safe_name}.lrcat'
    manifest_path = staging / 'LRAutomatic.catalog.json'

    try:
        staging.mkdir(parents=True, exist_ok=False)
        shutil.copy2(template, destination)
        if destination.stat().st_size != template.stat().st_size:
            raise OSError('Falha de integridade ao copiar o catálogo-modelo')

        manifest = {
            'schema_version': 1,
            'catalog_name': safe_name,
            'catalog_path': str((destination_dir / destination.name).resolve()),
            'template_path': str(template.resolve()),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'managed_by': 'LRAutomatic',
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.replace(destination_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    final_catalog = destination_dir / destination.name
    final_manifest = destination_dir / manifest_path.name
    launched = False
    if open_lightroom:
        executable = settings.lightroom_executable
        if not executable or not executable.is_file():
            raise FileNotFoundError('Configure lightroom_executable para abrir o catálogo automaticamente')
        subprocess.Popen([str(executable), str(final_catalog)], cwd=str(executable.parent), close_fds=True)
        launched = True

    return CatalogCreationResult(
        catalog_path=final_catalog,
        catalog_dir=destination_dir,
        manifest_path=final_manifest,
        launched=launched,
    )
