from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import (
    Document,
    DocumentExtraction,
    ProcessingRun,
    Project,
    ProjectDocument,
)
from app.schemas import (
    DocumentUploadResponse,
    HealthResponse,
    ProjectCreate,
    ProjectResponse,
    UploadedDocumentResponse,
)
from app.storage import FileTooLargeError, LocalFileStorage, PersistedFile, StagedUpload


router = APIRouter()


@dataclass(slots=True)
class DocumentResult:
    document: Document
    duplicate: bool


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="ok")


@router.post(
    "/api/v1/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = Project(id=payload.id or uuid.uuid4(), name=payload.name.strip())
    session.add(project)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project with this id already exists",
        ) from error
    await session.refresh(project)
    return project


def _request_hash(project_id: uuid.UUID, staged_files: list[StagedUpload]) -> str:
    canonical_files = sorted(
        (
            {
                "filename": item.original_filename,
                "sha256": item.checksum_sha256,
                "size_bytes": item.size_bytes,
            }
            for item in staged_files
        ),
        key=lambda item: (item["filename"], item["sha256"], item["size_bytes"]),
    )
    payload = json.dumps(
        {"project_id": str(project_id), "files": canonical_files},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _documents_by_ids(
    session: AsyncSession, document_ids: list[str]
) -> list[Document]:
    if not document_ids:
        return []
    ids = [uuid.UUID(document_id) for document_id in document_ids]
    result = await session.scalars(select(Document).where(Document.id.in_(ids)))
    by_id = {document.id: document for document in result.all()}
    return [by_id[document_id] for document_id in ids if document_id in by_id]


def _upload_response(
    project_id: uuid.UUID,
    run: ProcessingRun,
    results: list[DocumentResult],
) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        project_id=project_id,
        run_id=run.id,
        status="uploaded",
        documents=[
            UploadedDocumentResponse(
                id=result.document.id,
                original_filename=result.document.original_filename,
                media_type=result.document.media_type,
                size_bytes=result.document.size_bytes,
                sha256=result.document.checksum_sha256,
                version=result.document.version,
                duplicate=result.duplicate,
            )
            for result in results
        ],
    )


@router.post(
    "/api/v1/projects/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["documents"],
)
async def upload_project_documents(
    project_id: uuid.UUID,
    request: Request,
    files: Annotated[
        list[UploadFile], File(description="One or more project files of any format")
    ],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=128)
    ] = None,
    session: AsyncSession = Depends(get_session),
) -> DocumentUploadResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    settings = request.app.state.settings
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=413,
            detail=f"At most {settings.max_files_per_request} files are allowed",
        )

    storage = LocalFileStorage(
        settings.storage_root,
        settings.max_upload_size_bytes,
        settings.upload_chunk_size_bytes,
    )
    staged_files: list[StagedUpload] = []
    persisted_files: list[PersistedFile] = []

    try:
        for upload in files:
            staged_files.append(await storage.stage(upload))
    except FileTooLargeError as error:
        for staged in staged_files:
            storage.discard(staged)
        raise HTTPException(
            status_code=413,
            detail={
                "message": "File is too large",
                "filename": error.filename,
                "max_bytes": error.max_bytes,
            },
        ) from error
    except Exception:
        for staged in staged_files:
            storage.discard(staged)
        raise

    request_hash = _request_hash(project_id, staged_files)

    if idempotency_key:
        existing_run = await session.scalar(
            select(ProcessingRun).where(
                ProcessingRun.project_id == project_id,
                ProcessingRun.idempotency_key == idempotency_key,
            )
        )
        if existing_run is not None:
            for staged in staged_files:
                storage.discard(staged)
            if existing_run.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with different files",
                )
            documents = await _documents_by_ids(
                session, existing_run.input_document_ids
            )
            return _upload_response(
                project_id,
                existing_run,
                [DocumentResult(document=item, duplicate=True) for item in documents],
            )

    results: list[DocumentResult] = []
    documents_in_request: dict[tuple[str, int], Document] = {}

    try:
        for staged in staged_files:
            content_key = (staged.checksum_sha256, staged.size_bytes)
            if content_key in documents_in_request:
                storage.discard(staged)
                results.append(
                    DocumentResult(
                        document=documents_in_request[content_key], duplicate=True
                    )
                )
                continue

            existing_document = await session.scalar(
                select(Document)
                .join(ProjectDocument, ProjectDocument.document_id == Document.id)
                .where(
                    ProjectDocument.project_id == project_id,
                    Document.checksum_sha256 == staged.checksum_sha256,
                    Document.size_bytes == staged.size_bytes,
                )
                .limit(1)
            )
            if existing_document is not None:
                storage.discard(staged)
                documents_in_request[content_key] = existing_document
                results.append(
                    DocumentResult(document=existing_document, duplicate=True)
                )
                continue

            latest_version = await session.scalar(
                select(func.max(Document.version))
                .join(ProjectDocument, ProjectDocument.document_id == Document.id)
                .where(
                    ProjectDocument.project_id == project_id,
                    Document.original_filename == staged.original_filename,
                )
            )
            version = (latest_version or 0) + 1
            document_id = uuid.uuid4()
            persisted = storage.persist(
                staged, project_id=project_id, document_id=document_id, version=version
            )
            persisted_files.append(persisted)

            document = Document(
                id=document_id,
                original_filename=staged.original_filename,
                stored_filename=staged.stored_filename,
                media_type=staged.media_type,
                size_bytes=staged.size_bytes,
                checksum_sha256=staged.checksum_sha256,
                storage_uri=persisted.storage_uri,
                version=version,
            )
            session.add_all(
                [
                    document,
                    ProjectDocument(project_id=project_id, document_id=document_id),
                    DocumentExtraction(document_id=document_id, status="pending"),
                ]
            )
            documents_in_request[content_key] = document
            results.append(DocumentResult(document=document, duplicate=False))

        unique_document_ids = list(
            dict.fromkeys(str(result.document.id) for result in results)
        )
        run = ProcessingRun(
            project_id=project_id,
            status="uploaded",
            current_step="uploaded",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_document_ids=unique_document_ids,
            errors=[],
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
    except IntegrityError as error:
        await session.rollback()
        for staged in staged_files:
            storage.discard(staged)
        for persisted in persisted_files:
            storage.remove_persisted(persisted)
        raise HTTPException(
            status_code=409,
            detail="Upload conflicts with an existing request",
        ) from error
    except Exception:
        await session.rollback()
        for staged in staged_files:
            storage.discard(staged)
        for persisted in persisted_files:
            storage.remove_persisted(persisted)
        raise

    return _upload_response(project_id, run, results)
