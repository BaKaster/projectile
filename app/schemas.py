from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=300)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime


class UploadedDocumentResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    version: int
    duplicate: bool


class DocumentUploadResponse(BaseModel):
    project_id: uuid.UUID
    run_id: uuid.UUID
    status: Literal["uploaded"]
    documents: list[UploadedDocumentResponse]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
