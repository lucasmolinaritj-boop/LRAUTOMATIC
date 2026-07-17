from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

PHOTO_EXTENSIONS = {'.arw','.cr2','.cr3','.dng','.heic','.heif','.jpeg','.jpg','.nef','.orf','.raf','.rw2','.tif','.tiff'}

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class JobStatus(StrEnum):
    QUEUED='queued'; RUNNING='running'; COMPLETED='completed'; PARTIAL='partial'; FAILED='failed'; CANCELLED='cancelled'
class SourceStatus(StrEnum):
    QUEUED='queued'; RUNNING='running'; COMPLETED='completed'; FAILED='failed'; CANCELLED='cancelled'

class ImportSource(BaseModel):
    path: str
    collection: str|None=None
    recursive: bool|None=None
    keywords: list[str]=Field(default_factory=list)
    @field_validator('path')
    @classmethod
    def normalize_path(cls,value:str)->str:
        path=Path(value).expanduser().resolve()
        if not path.is_dir(): raise ValueError(f'Pasta não encontrada: {path}')
        return str(path)

class ImportJobRequest(BaseModel):
    sources:list[ImportSource]=Field(min_length=1)
    collection_set:str|None=None
    recursive:bool=False
    create_collections:bool=True
    build_smart_previews:bool=False
    develop_preset_name:str|None=None
    develop_preset_uuid:str|None=None
    duplicate_policy:str='skip'

class SourceProgress(BaseModel):
    path:str
    collection:str|None=None
    status:SourceStatus=SourceStatus.QUEUED
    discovered:int=0; imported:int=0; skipped:int=0; failed:int=0
    error:str|None=None

class JobEvent(BaseModel):
    at:str=Field(default_factory=utc_now)
    stage:str
    title:str
    detail:str|None=None
    level:str='info'

class ImportJob(BaseModel):
    schema_version:int=3
    job_id:str=Field(default_factory=lambda:f'job_{uuid4().hex}')
    created_at:str=Field(default_factory=utc_now)
    updated_at:str=Field(default_factory=utc_now)
    started_at:str|None=None
    finished_at:str|None=None
    status:JobStatus=JobStatus.QUEUED
    request:ImportJobRequest
    progress:list[SourceProgress]=Field(default_factory=list)
    events:list[JobEvent]=Field(default_factory=list)
    total_discovered:int=0; total_imported:int=0; total_skipped:int=0; total_failed:int=0
    current_source:str|None=None
    active_catalog_path:str|None=None
    error:str|None=None
    preset_status:str='not_requested'
    preset_name_applied:str|None=None
    preset_applied_count:int=0
    smart_previews_status:str='not_requested'
    smart_previews_created:int=0; smart_previews_existed:int=0; smart_previews_failed:int=0
    def touch(self)->None: self.updated_at=utc_now()
    def add_event(self,stage:str,title:str,detail:str|None=None,level:str='info')->None:
        event=JobEvent(stage=stage,title=title,detail=detail,level=level)
        last=self.events[-1] if self.events else None
        if not last or (last.stage,last.title,last.detail,last.level)!=(event.stage,event.title,event.detail,event.level):
            self.events.append(event)
            self.events=self.events[-300:]
        self.touch()
