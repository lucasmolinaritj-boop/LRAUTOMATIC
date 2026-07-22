from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_PHOTO_EXTENSIONS = ['cr2', 'cr3', 'dng']
KNOWN_PHOTO_EXTENSIONS = {
    'arw', 'cr2', 'cr3', 'dng', 'heic', 'heif', 'jpeg', 'jpg',
    'nef', 'orf', 'raf', 'rw2', 'tif', 'tiff',
}


def normalize_extensions(values: list[str] | tuple[str, ...] | set[str] | str | None) -> list[str]:
    if values is None:
        return list(DEFAULT_PHOTO_EXTENSIONS)
    if isinstance(values, str):
        values = values.replace(';', ',').split(',')
    normalized: list[str] = []
    for value in values:
        extension = str(value).strip().lower().lstrip('.')
        if extension and extension not in normalized:
            normalized.append(extension)
    if not normalized:
        raise ValueError('Informe ao menos uma extensão de foto.')
    return normalized


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    PARTIAL = 'partial'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    INTERRUPTED = 'interrupted'


class SourceStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class ImportSource(BaseModel):
    model_config = ConfigDict(extra='allow')

    path: str
    collection: str | None = None
    recursive: bool | None = None
    keywords: list[str] = Field(default_factory=list)
    expected_count: int = Field(default=0, ge=0)
    work_id: str | None = None
    photographer: str | None = None
    client: str | None = None
    service_name: str | None = None
    scheduled_at: str | None = None

    @field_validator('path')
    @classmethod
    def normalize_path(cls, value: str) -> str:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f'Pasta não encontrada: {path}')
        return str(path)


class ImportJobRequest(BaseModel):
    model_config = ConfigDict(extra='allow')

    sources: list[ImportSource] = Field(min_length=1)
    collection_set: str | None = None
    recursive: bool = False
    create_collections: bool = True
    organize_collections_by_photographer: bool = False
    organize_collections_by_client: bool = False
    collection_organization_version: int = 0
    build_standard_previews: bool = True
    standard_preview_size: int = Field(default=2048, ge=256, le=16384)
    build_smart_previews: bool = False
    allowed_extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_PHOTO_EXTENSIONS))
    develop_preset_name: str | None = None
    develop_preset_uuid: str | None = None
    duplicate_policy: str = 'skip'

    @field_validator('allowed_extensions', mode='before')
    @classmethod
    def validate_allowed_extensions(cls, value):
        return normalize_extensions(value)


class SourceProgress(BaseModel):
    model_config = ConfigDict(extra='allow')

    path: str
    collection: str | None = None
    status: SourceStatus = SourceStatus.QUEUED
    discovered: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None
    discovered_files: list[str] = Field(default_factory=list)
    scan_completed: bool = False
    scan_completed_at: str | None = None


class JobEvent(BaseModel):
    model_config = ConfigDict(extra='allow')

    at: str = Field(default_factory=utc_now)
    stage: str
    title: str
    detail: str | None = None
    level: str = 'info'


class BadFileRecord(BaseModel):
    model_config = ConfigDict(extra='allow')

    path: str
    reason: str
    at: str = Field(default_factory=utc_now)


class ImportJob(BaseModel):
    model_config = ConfigDict(extra='allow')

    schema_version: int = 11
    job_id: str = Field(default_factory=lambda: f'job_{uuid4().hex}')
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    status: JobStatus = JobStatus.QUEUED
    request: ImportJobRequest
    progress: list[SourceProgress] = Field(default_factory=list)
    events: list[JobEvent] = Field(default_factory=list)
    total_discovered: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    current_source: str | None = None
    current_photo: str | None = None
    current_stage: str | None = None
    active_catalog_path: str | None = None
    error: str | None = None
    runner_instance_id: str | None = None
    runner_heartbeat_epoch: int | None = None
    runner_heartbeat_at: str | None = None
    interrupted_at: str | None = None
    import_attempts_total: int = 0
    inventory_reused_count: int = 0
    bad_files: list[BadFileRecord] = Field(default_factory=list)
    bad_files_count: int = 0
    completed_with_file_errors: bool = False
    preset_status: str = 'not_requested'
    preset_name_applied: str | None = None
    preset_applied_count: int = 0
    preset_candidate_count: int = 0
    preset_skipped_existing_count: int = 0
    standard_previews_status: str = 'not_requested'
    standard_previews_created: int = 0
    standard_previews_failed: int = 0
    standard_previews_attempts_total: int = 0
    smart_previews_status: str = 'not_requested'
    smart_previews_created: int = 0
    smart_previews_existed: int = 0
    smart_previews_failed: int = 0
    smart_preview_recheck_skipped: int = 0
    collections_status: str = 'not_requested'
    collections_created: int = 0
    collection_sets_created: int = 0
    collections_organization_version: int = 0
    collections_run_once_token: str | None = None

    def touch(self) -> None:
        self.updated_at = utc_now()

    def add_event(self, stage: str, title: str, detail: str | None = None, level: str = 'info') -> None:
        event = JobEvent(stage=stage, title=title, detail=detail, level=level)
        last = self.events[-1] if self.events else None
        if not last or (last.stage, last.title, last.detail, last.level) != (event.stage, event.title, event.detail, event.level):
            self.events.append(event)
            self.events = self.events[-300:]
        self.touch()
