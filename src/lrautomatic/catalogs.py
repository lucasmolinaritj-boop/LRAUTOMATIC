from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import Settings

INVALID_NAME_CHARS = '<>:"/\\|?*'
PHOTO_EXTENSIONS = {
    '.arw', '.cr2', '.cr3', '.dng', '.heic', '.heif', '.jpeg', '.jpg',
    '.nef', '.orf', '.raf', '.rw2', '.tif', '.tiff'
}


@dataclass(slots=True)
class CatalogCreationResult:
    catalog_path: Path
    catalog_dir: Path
    manifest_path: Path
    launched: bool
    catalog_date: date
    catalog_name: str


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


def _parse_manual_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = value.strip()
    for pattern in ('%d%m%Y', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError('Data inválida. Use DDMMAAAA, DD/MM/AAAA ou AAAA-MM-DD')


def earliest_photo_date(source_paths: list[str | Path], *, recursive: bool = True) -> date | None:
    """Retorna a data mais antiga dos arquivos de foto encontrados.

    Usa a data de modificação do arquivo, que funciona sem dependências extras e é
    apropriada para a pasta de entrega/importação. Pastas de períodos diferentes
    resultam na primeira data encontrada cronologicamente.
    """
    earliest: datetime | None = None
    for raw_path in source_paths:
        root = Path(raw_path).expanduser().resolve()
        if not root.exists():
            continue
        candidates = root.rglob('*') if recursive else root.glob('*')
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime)
            except OSError:
                continue
            if earliest is None or modified < earliest:
                earliest = modified
    return earliest.date() if earliest else None


def resolve_catalog_date(
    settings: Settings,
    *,
    source_paths: list[str | Path] | None = None,
    manual_date: str | date | datetime | None = None,
    recursive: bool = True,
) -> date:
    forced = _parse_manual_date(manual_date)
    if forced:
        return forced
    if settings.catalog_date_source == 'earliest_file' and source_paths:
        detected = earliest_photo_date(source_paths, recursive=recursive)
        if detected:
            return detected
    return datetime.now().date()


def build_catalog_name(settings: Settings, catalog_date: date, *, label: str | None = None) -> str:
    values = {
        'date': catalog_date.strftime(settings.catalog_date_format),
        'dd': catalog_date.strftime('%d'),
        'mm': catalog_date.strftime('%m'),
        'yyyy': catalog_date.strftime('%Y'),
        'label': (label or '').strip(),
    }
    try:
        rendered = settings.catalog_naming_template.format(**values)
    except KeyError as exc:
        raise ValueError(f'Token inválido em catalog_naming_template: {exc.args[0]}') from exc
    return safe_catalog_name(' '.join(rendered.split()))


def _available_catalog_name(root: Path, base_name: str) -> str:
    if not (root / base_name).exists():
        return base_name
    counter = 2
    while True:
        candidate = f'{base_name} {counter:02d}'
        if not (root / candidate).exists():
            return candidate
        counter += 1


def create_catalog(
    settings: Settings,
    name: str | None = None,
    *,
    source_paths: list[str | Path] | None = None,
    catalog_date: str | date | datetime | None = None,
    label: str | None = None,
    recursive: bool = True,
    open_lightroom: bool = True,
) -> CatalogCreationResult:
    """Cria um catálogo atômico a partir do modelo oficial.

    Quando ``name`` não é informado, usa ``catalog_naming_template``. O token
    ``{date}`` representa a primeira data das fotos ou a data manual informada.
    """
    template = settings.catalog_template
    root = settings.catalog_output_root
    if not template or not template.is_file():
        raise FileNotFoundError('Configure catalog_template apontando para um catálogo vazio válido')
    if not root:
        raise ValueError('Configure catalog_output_root')
    _validate_template(template)

    chosen_date = resolve_catalog_date(
        settings,
        source_paths=source_paths,
        manual_date=catalog_date,
        recursive=recursive,
    )
    requested_name = safe_catalog_name(name) if name else build_catalog_name(settings, chosen_date, label=label)
    root.mkdir(parents=True, exist_ok=True)
    safe_name = _available_catalog_name(root, requested_name)
    destination_dir = root / safe_name

    staging = root / f'.{safe_name}.creating-{uuid4().hex[:8]}'
    destination = staging / f'{safe_name}.lrcat'
    manifest_path = staging / 'LRAutomatic.catalog.json'

    try:
        staging.mkdir(parents=True, exist_ok=False)
        shutil.copy2(template, destination)
        if destination.stat().st_size != template.stat().st_size:
            raise OSError('Falha de integridade ao copiar o catálogo-modelo')

        manifest = {
            'schema_version': 2,
            'catalog_name': safe_name,
            'catalog_path': str((destination_dir / destination.name).resolve()),
            'catalog_date': chosen_date.isoformat(),
            'catalog_naming_template': settings.catalog_naming_template,
            'catalog_date_source': 'manual' if catalog_date else settings.catalog_date_source,
            'source_paths': [str(Path(path).expanduser().resolve()) for path in (source_paths or [])],
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
        catalog_date=chosen_date,
        catalog_name=safe_name,
    )
