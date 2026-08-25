from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.analysis_contracts import (
    DataGap,
    DataIssue,
    DocumentDigest,
    ExtractedFact,
    MaterialQuestion,
    StageSignalEvidence,
)
from app.stage_contracts import ProjectStagePlan, StagePlanContext
from app.work_contracts import GeneratedWorkPlan, WorkPlanContext


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
    source_path: str
    media_type: str
    size_bytes: int
    sha256: str
    version: int
    duplicate: bool


class DocumentUploadResponse(BaseModel):
    project_id: uuid.UUID
    upload_run_id: uuid.UUID
    status: Literal["uploaded"]
    documents: list[UploadedDocumentResponse]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


AnalysisStatus = Literal[
    "queued", "extracting", "analyzing", "requires_input", "ready", "failed"
]


class AnalysisRunCreate(BaseModel):
    force_reextract: bool = False
    question_policy: Literal["material_only"] = "material_only"


class AnalysisRunAccepted(BaseModel):
    run_id: uuid.UUID
    project_id: uuid.UUID
    status: AnalysisStatus
    document_ids: list[uuid.UUID]


class ProjectAnalysisResponse(BaseModel):
    id: uuid.UUID
    project_name: str | None = None
    project_type_code: str | None
    confidence: Literal["low", "medium", "high"]
    summary: str
    rationale: str
    facts: list[ExtractedFact]
    assumptions: list[str]
    issues: list[DataIssue]
    gaps: list[DataGap]
    questions: list[MaterialQuestion]
    warnings: list[str]
    stage_signals: list[StageSignalEvidence]
    stage_plan: ProjectStagePlan | None = None
    work_plan: GeneratedWorkPlan | None = None
    document_digests: list[DocumentDigest]
    source_document_ids: list[uuid.UUID]
    model_name: str
    prompt_version: str
    created_at: datetime


class StagePlanRequest(StagePlanContext):
    pass


class WorkPlanRequest(BaseModel):
    stage_context: StagePlanContext = Field(default_factory=StagePlanContext)
    work_context: WorkPlanContext = Field(default_factory=WorkPlanContext)
    effort_mode: Literal["auto", "deterministic"] = "auto"


class AnalysisRunResponse(BaseModel):
    run_id: uuid.UUID
    project_id: uuid.UUID
    status: AnalysisStatus
    current_step: str
    document_ids: list[uuid.UUID]
    errors: list[dict]
    result: ProjectAnalysisResponse | None = None
    created_at: datetime
    updated_at: datetime


class ChatCreate(BaseModel):
    name: str | None = Field(default=None, max_length=300)


class ChatUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=300)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    role: Literal["user", "assistant", "system"]
    kind: Literal["query", "answer", "system"]
    content: str
    created_at: datetime


class ChatMessageAccepted(BaseModel):
    message: ChatMessageResponse
    analysis: AnalysisRunAccepted


class QuestionAnswerResolved(BaseModel):
    message: ChatMessageResponse
    analysis: AnalysisRunResponse


class ChatSummary(BaseModel):
    id: uuid.UUID
    name: str
    last_message: str | None
    latest_status: AnalysisStatus | None
    created_at: datetime
    updated_at: datetime


class ChatDetail(BaseModel):
    id: uuid.UUID
    name: str
    messages: list[ChatMessageResponse]
    latest_analysis: AnalysisRunResponse | None
    created_at: datetime
    updated_at: datetime


class QuestionAnswerCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)


class ProjectTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    direction_code: str
    name: str
    details: str | None


class ProjectTypeUpdate(BaseModel):
    project_type_code: str = Field(min_length=1, max_length=100)


class RateImportItem(BaseModel):
    role_code: str | None = None
    role_name: str
    external_id: int | None = None
    sale_rate: int = Field(gt=0)
    cost_rate: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    source: str
    selected: bool = True
    eligible_for_auto_apply: bool = False


class RateImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    status: str
    auto_apply: bool
    applied_count: int
    extracted_items: list[RateImportItem]
    created_at: datetime


class RateImportApply(BaseModel):
    items: list[RateImportItem] = Field(default_factory=list)
