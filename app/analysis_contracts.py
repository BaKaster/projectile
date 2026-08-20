from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.work_contracts import ProjectSpecificWork

Confidence = Literal["low", "medium", "high"]
GapImpact = Literal["low", "medium", "high", "critical"]


class ExtractedFact(BaseModel):
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list)


class DataGap(BaseModel):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    impact: GapImpact
    changes_estimate: bool
    blocking: bool
    can_use_assumption: bool
    suggested_assumption: str | None = None
    question: str | None = None


class DataIssue(BaseModel):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    impact_on_estimate: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list)
    recommended_action: str | None = None


class StageSignalEvidence(BaseModel):
    code: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list)


class ModelAnalysis(BaseModel):
    project_type_code: str | None = None
    confidence: Confidence
    summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    facts: list[ExtractedFact] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    issues: list[DataIssue] = Field(default_factory=list)
    gaps: list[DataGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stage_signals: list[StageSignalEvidence] = Field(default_factory=list)
    project_specific_works: list[ProjectSpecificWork] = Field(default_factory=list)


class DocumentDigest(BaseModel):
    document_id: str
    filename: str
    role: str
    summary: str = Field(min_length=1)
    key_facts: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    missing_for_estimate: list[str] = Field(default_factory=list)


class DigestBatch(BaseModel):
    documents: list[DocumentDigest] = Field(default_factory=list)


class MaterialQuestion(BaseModel):
    code: str
    question: str
    reason: str
    blocking: bool


def material_questions(gaps: list[DataGap], limit: int = 5) -> list[MaterialQuestion]:
    """Keep only questions whose answers can materially alter the estimate."""
    result: list[MaterialQuestion] = []
    for gap in gaps:
        if (
            not gap.question
            or not gap.changes_estimate
            or gap.impact not in {"high", "critical"}
            or (gap.can_use_assumption and not gap.blocking)
        ):
            continue
        result.append(
            MaterialQuestion(
                code=gap.code,
                question=gap.question,
                reason=gap.description,
                blocking=gap.blocking,
            )
        )
        if len(result) == limit:
            break
    return result
