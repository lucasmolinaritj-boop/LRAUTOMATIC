from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


PHOTO_EXTENSIONS = {
    ".arw", ".cr2", ".cr3", ".dng", ".heic", ".heif", ".jpeg", ".jpg",
    ".nef", ".orf", ".raf", ".rw2", ".tif", ".tiff"
}


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportSource(BaseModel):
    path: str
    collection: str | None = None
    recursive: bool | None = None
    keywords: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Pasta não encontrada: {path}")
        return str(path)


class ImportJobRequest(BaseModel):
    sources: list[ImportSource] = Field(min_length=1)
    collection_set: str | None = None
    recursive: bool = False
    create_collections: bool = True
    build_smart_previews: bool = False
    duplicate_policy: str = "skip"


class SourceProgress(BaseModel):
    path: str
    collection: str | None = None
    status: SourceStatus = SourceStatus.QUEUED
    discovered: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None


class ImportJob(BaseModel):
    schema_version: int = 1
    job_id: str = Field(default_factory=lambda: f"job_{uuid4().hex}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: JobStatus = JobStatus.QUEUED
    request: ImportJobRequest
    progress: list[SourceProgress] = Field(default_factory=list)
    total_discovered: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    current_source: str | None = None
    error: str | None = None
    smart_previews_status: str = "not_requested"

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
