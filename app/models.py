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


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
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
