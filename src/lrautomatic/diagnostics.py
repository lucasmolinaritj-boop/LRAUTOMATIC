from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .config import Settings


SENSITIVE_KEYS = {'api_key', 'authorization', 'token', 'password', 'secret'}


def _redact(value):
    if isinstance(value, dict):
        return {k: ('***REDACTED***' if k.lower() in SENSITIVE_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=12, check=False)
        return (completed.stdout + '\n' + completed.stderr).strip()
    except Exception as exc:
        return f'ERROR: {type(exc).__name__}: {exc}'


def _copy_diagnostic_file(source: Path, destination: Path) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == '.json':
            data = json.loads(source.read_text(encoding='utf-8'))
            destination.write_text(json.dumps(_redact(data), ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            destination.write_bytes(source.read_bytes())
    except Exception as exc:
        destination.with_suffix(destination.suffix + '.error.txt').write_text(str(exc), encoding='utf-8')


def create_diagnostic_zip(settings: Settings, config_path: str | Path = 'config.json', output_dir: Path | None = None) -> Path:
    output_dir = output_dir or Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_path = output_dir / f'LRAutomatic_Diagnostico_{stamp}.zip'

    with tempfile.TemporaryDirectory(prefix='lrautomatic_diag_') as temp:
        root = Path(temp)
        system_info = {
            'created_at': datetime.now().isoformat(),
            'python': sys.version,
            'executable': sys.executable,
            'platform': platform.platform(),
            'machine': platform.machine(),
            'processor': platform.processor(),
            'cwd': os.getcwd(),
            'data_dir': str(settings.data_dir),
            'catalog_template': str(settings.catalog_template) if settings.catalog_template else None,
            'catalog_output_root': str(settings.catalog_output_root) if settings.catalog_output_root else None,
            'lightroom_executable': str(settings.lightroom_executable) if settings.lightroom_executable else None,
            'lightroom_exists': bool(settings.lightroom_executable and settings.lightroom_executable.exists()),
            'template_exists': bool(settings.catalog_template and settings.catalog_template.exists()),
        }
        (root / 'system.json').write_text(json.dumps(system_info, ensure_ascii=False, indent=2), encoding='utf-8')
        (root / 'pip_freeze.txt').write_text(_command_output([sys.executable, '-m', 'pip', 'freeze']), encoding='utf-8')
        (root / 'processes.txt').write_text(_command_output(['tasklist']), encoding='utf-8')
        (root / 'ports.txt').write_text(_command_output(['netstat', '-ano']), encoding='utf-8')

        config_file = Path(config_path)
        if config_file.exists():
            try:
                raw = json.loads(config_file.read_text(encoding='utf-8'))
                (root / 'config.redacted.json').write_text(json.dumps(_redact(raw), ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as exc:
                (root / 'config_error.txt').write_text(str(exc), encoding='utf-8')

        # Root-level traces are intentionally included because the Lightroom SDK may
        # fail before the normal logs/ and plugin_state/ directories are usable.
        if settings.data_dir.exists():
            for source in sorted((p for p in settings.data_dir.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                _copy_diagnostic_file(source, root / 'data_root' / source.name)

        for folder_name in ('jobs', 'responses', 'control', 'logs', 'plugin_state'):
            source = settings.data_dir / folder_name
            if source.exists():
                target = root / folder_name
                target.mkdir(parents=True, exist_ok=True)
                files = sorted((p for p in source.rglob('*') if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:200]
                for file in files:
                    _copy_diagnostic_file(file, target / file.relative_to(source))

        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for file in root.rglob('*'):
                if file.is_file():
                    archive.write(file, file.relative_to(root))

    return zip_path
