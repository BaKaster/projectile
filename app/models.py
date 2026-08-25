from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_is_generated: Mapped[bool] = mapped_column(nullable=False, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="query")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_messages_role",
        ),
        CheckConstraint(
            "kind IN ('query', 'answer', 'system')",
            name="ck_chat_messages_kind",
        ),
        Index("ix_chat_messages_project_created", "project_id", "created_at"),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(
        String(255), nullable=False, default="application/octet-stream"
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_documents_size_nonnegative"),
        CheckConstraint("version > 0", name="ck_documents_version_positive"),
        Index("ix_documents_checksum_sha256", "checksum_sha256"),
    )


class ProjectDocument(Base):
    __tablename__ = "project_documents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_project_documents_document_id", "document_id"),)


class DocumentExtraction(TimestampMixin, Base):
    __tablename__ = "document_extractions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    tables: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'extracting', 'ready', 'unsupported', 'failed')",
            name="ck_document_extractions_status",
        ),
    )


class AnalysisRun(TimestampMixin, Base):
    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    input_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    force_reextract: Mapped[bool] = mapped_column(nullable=False, default=False)
    question_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="material_only"
    )
    model_name: Mapped[str | None] = mapped_column(String(100))
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'extracting', 'analyzing', 'requires_input', "
            "'ready', 'failed')",
            name="ck_analysis_runs_status",
        ),
        Index("ix_analysis_runs_status_created", "status", "created_at"),
        Index("ix_analysis_runs_project_created", "project_id", "created_at"),
    )


class ProjectAnalysis(Base):
    __tablename__ = "project_analyses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_type_code: Mapped[str | None] = mapped_column(
        ForeignKey("project_types.code", ondelete="SET NULL")
    )
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    document_digests: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    source_document_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IN ('low', 'medium', 'high')",
            name="ck_project_analyses_confidence",
        ),
        Index("ix_project_analyses_project_created", "project_id", "created_at"),
    )


class ProcessingRun(TimestampMixin, Base):
    __tablename__ = "processing_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    current_step: Mapped[str] = mapped_column(
        String(64), nullable=False, default="uploaded"
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'extracting', 'analyzing', 'requires_input', "
            "'generating', 'validating', 'exporting', 'ready', 'failed')",
            name="ck_processing_runs_status",
        ),
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_processing_runs_idempotency"
        ),
        Index("ix_processing_runs_project_created", "project_id", "created_at"),
    )


class ProjectType(TimestampMixin, Base):
    __tablename__ = "project_types"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    direction_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_project_types_direction", "direction_code"),)


class LaborRole(TimestampMixin, Base):
    """Stable role identity; commercial values are versioned separately."""
    __tablename__ = "labor_roles"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    external_id: Mapped[int | None] = mapped_column(Integer, unique=True)


class RoleRate(TimestampMixin, Base):
    __tablename__ = "role_rates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    role_code: Mapped[str] = mapped_column(ForeignKey("labor_roles.code", ondelete="CASCADE"), nullable=False)
    sale_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_import_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    __table_args__ = (CheckConstraint("sale_rate > 0 AND cost_rate > 0", name="ck_role_rates_positive"), Index("ix_role_rates_role_effective", "role_code", "effective_from"))


class RateImport(TimestampMixin, Base):
    __tablename__ = "rate_imports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="review")
    auto_apply: Mapped[bool] = mapped_column(nullable=False, default=False)
    extracted_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (CheckConstraint("status IN ('review', 'applied')", name="ck_rate_imports_status"),)
