from __future__ import annotations

import hashlib
import json
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import WithJsonSchema
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import (
    AnalysisRun,
    ChatMessage,
    Document,
    DocumentExtraction,
    ProcessingRun,
    Project,
    ProjectAnalysis,
    ProjectDocument,
)
from app.schemas import (
    AnalysisRunAccepted,
    AnalysisRunCreate,
    AnalysisRunResponse,
    ChatCreate,
    ChatDetail,
    ChatMessageAccepted,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSummary,
    DocumentUploadResponse,
    HealthResponse,
    ProjectCreate,
    ProjectResponse,
    QuestionAnswerCreate,
    StagePlanRequest,
    UploadedDocumentResponse,
)
from app.stage_contracts import ProjectStagePlan
from app.stage_planner import StagePlanningError
from app.storage import FileTooLargeError, LocalFileStorage, PersistedFile, StagedUpload

SwaggerUploadFile = Annotated[
    UploadFile,
    WithJsonSchema({"type": "string", "format": "binary"}),
]

router = APIRouter()
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/api/v1/project-types/{project_type_code}/stage-plan",
    response_model=ProjectStagePlan,
    tags=["stages"],
)
async def build_project_stage_plan(
    project_type_code: str,
    payload: StagePlanRequest,
    request: Request,
) -> ProjectStagePlan:
    try:
        return request.app.state.stage_planner.build_plan(project_type_code, payload)
    except StagePlanningError as error:
        status_code = 404 if str(error).startswith("unknown project type") else 422
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@dataclass(slots=True)
class DocumentResult:
    document: Document
    duplicate: bool


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(session: SessionDependency) -> HealthResponse:
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
    session: SessionDependency,
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


def _analysis_accepted(run: AnalysisRun) -> AnalysisRunAccepted:
    return AnalysisRunAccepted(
        run_id=run.id,
        project_id=run.project_id,
        status=run.status,
        document_ids=[uuid.UUID(item) for item in run.input_document_ids],
    )


async def _queue_project_analysis(
    session: AsyncSession, project_id: uuid.UUID
) -> AnalysisRun:
    documents = await _latest_project_documents(session, project_id)
    if not documents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project has no documents to analyze",
        )
    run = AnalysisRun(
        project_id=project_id,
        status="queued",
        current_step="queued",
        input_document_ids=[str(document.id) for document in documents],
        force_reextract=False,
        question_policy="material_only",
        errors=[],
    )
    session.add(run)
    await session.flush()
    return run


async def _append_chat_message(
    session: AsyncSession,
    project: Project,
    content: str,
    *,
    kind: str,
) -> ChatMessage:
    normalized = content.strip()
    message = ChatMessage(
        project_id=project.id,
        role="user",
        kind=kind,
        content=normalized,
    )
    session.add(message)
    await session.flush()

    raw = normalized.encode("utf-8")
    document_id = uuid.uuid4()
    filename = "Ответ на вопросы.txt" if kind == "answer" else "Запрос пользователя.txt"
    source_path = f"chat/{message.id}.txt"
    document = Document(
        id=document_id,
        original_filename=filename,
        source_path=source_path,
        stored_filename=f"{document_id}.txt",
        media_type="text/plain; charset=utf-8",
        size_bytes=len(raw),
        checksum_sha256=hashlib.sha256(raw).hexdigest(),
        storage_uri=f"chat://{project.id}/{document_id}",
        version=1,
    )
    session.add_all(
        [
            document,
            ProjectDocument(project_id=project.id, document_id=document_id),
            DocumentExtraction(
                document_id=document_id,
                status="ready",
                extractor_version="chat-v1",
                extracted_text=normalized,
                tables=[],
                errors=[],
            ),
        ]
    )
    project.updated_at = datetime.now(timezone.utc)
    if project.name == "Новый чат" and kind == "query":
        project.name = normalized[:80] + ("…" if len(normalized) > 80 else "")
    await session.flush()
    return message


@router.post(
    "/api/v1/chats",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["chats"],
)
async def create_chat(payload: ChatCreate, session: SessionDependency) -> Project:
    project = Project(name=(payload.name or "Новый чат").strip() or "Новый чат")
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/api/v1/chats", response_model=list[ChatSummary], tags=["chats"])
async def list_chats(session: SessionDependency) -> list[ChatSummary]:
    projects = (
        await session.scalars(select(Project).order_by(Project.updated_at.desc()))
    ).all()
    result: list[ChatSummary] = []
    for project in projects:
        last_message = await session.scalar(
            select(ChatMessage)
            .where(ChatMessage.project_id == project.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        latest_run = await session.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.project_id == project.id)
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        )
        result.append(
            ChatSummary(
                id=project.id,
                name=project.name,
                last_message=last_message.content if last_message else None,
                latest_status=latest_run.status if latest_run else None,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
        )
    return result


@router.get("/api/v1/chats/{chat_id}", response_model=ChatDetail, tags=["chats"])
async def get_chat(chat_id: uuid.UUID, session: SessionDependency) -> ChatDetail:
    project = await session.get(Project, chat_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    messages = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.project_id == chat_id)
            .order_by(ChatMessage.created_at)
        )
    ).all()
    run = await session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == chat_id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    )
    latest_analysis = None
    if run is not None:
        analysis = await session.scalar(
            select(ProjectAnalysis).where(ProjectAnalysis.run_id == run.id)
        )
        latest_analysis = _analysis_run_response(run, analysis)
    return ChatDetail(
        id=project.id,
        name=project.name,
        messages=[ChatMessageResponse.model_validate(message) for message in messages],
        latest_analysis=latest_analysis,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post(
    "/api/v1/chats/{chat_id}/messages",
    response_model=ChatMessageAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["chats"],
)
async def send_chat_message(
    chat_id: uuid.UUID,
    payload: ChatMessageCreate,
    session: SessionDependency,
) -> ChatMessageAccepted:
    project = await session.get(Project, chat_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    message = await _append_chat_message(
        session, project, payload.content, kind="query"
    )
    run = await _queue_project_analysis(session, project.id)
    await session.commit()
    await session.refresh(message)
    return ChatMessageAccepted(
        message=ChatMessageResponse.model_validate(message),
        analysis=_analysis_accepted(run),
    )


def _request_hash(project_id: uuid.UUID, staged_files: list[StagedUpload]) -> str:
    canonical_files = sorted(
        (
            {
                "filename": item.original_filename,
                "source_path": item.source_path,
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
        upload_run_id=run.id,
        status="uploaded",
        documents=[
            UploadedDocumentResponse(
                id=result.document.id,
                original_filename=result.document.original_filename,
                source_path=result.document.source_path,
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
        list[SwaggerUploadFile],
        File(description="One or more project files of any format"),
    ],
    session: SessionDependency,
    relative_paths: Annotated[
        list[str] | None,
        Form(description="Optional relative path for every file, in the same order"),
    ] = None,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=128)
    ] = None,
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
    if relative_paths is not None and len(relative_paths) != len(files):
        raise HTTPException(
            status_code=422,
            detail="relative_paths must contain exactly one value for every file",
        )

    storage = LocalFileStorage(
        settings.storage_root,
        settings.max_upload_size_bytes,
        settings.upload_chunk_size_bytes,
    )
    staged_files: list[StagedUpload] = []
    persisted_files: list[PersistedFile] = []

    try:
        for index, upload in enumerate(files):
            source_path = relative_paths[index] if relative_paths is not None else upload.filename
            staged_files.append(await storage.stage(upload, source_path=source_path))
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
        content_keys = list(
            dict.fromkeys(
                (staged.checksum_sha256, staged.size_bytes)
                for staged in staged_files
            )
        )
        existing_documents = (
            await session.scalars(
                select(Document)
                .join(ProjectDocument, ProjectDocument.document_id == Document.id)
                .where(
                    ProjectDocument.project_id == project_id,
                    tuple_(Document.checksum_sha256, Document.size_bytes).in_(
                        content_keys
                    ),
                )
                .order_by(Document.version.desc(), Document.created_at.desc())
            )
        ).all()
        existing_by_content: dict[tuple[str, int], Document] = {}
        for document in existing_documents:
            existing_by_content.setdefault(
                (document.checksum_sha256, document.size_bytes), document
            )

        version_keys = list(
            dict.fromkeys(
                (staged.original_filename, staged.source_path)
                for staged in staged_files
            )
        )
        version_rows = await session.execute(
            select(
                Document.original_filename,
                Document.source_path,
                func.max(Document.version),
            )
            .join(ProjectDocument, ProjectDocument.document_id == Document.id)
            .where(
                ProjectDocument.project_id == project_id,
                tuple_(Document.original_filename, Document.source_path).in_(
                    version_keys
                ),
            )
            .group_by(Document.original_filename, Document.source_path)
        )
        latest_versions = {
            (filename, source_path): version
            for filename, source_path, version in version_rows
        }

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

            existing_document = existing_by_content.get(content_key)
            if existing_document is not None:
                storage.discard(staged)
                documents_in_request[content_key] = existing_document
                results.append(
                    DocumentResult(document=existing_document, duplicate=True)
                )
                continue

            version_key = (staged.original_filename, staged.source_path)
            version = latest_versions.get(version_key, 0) + 1
            latest_versions[version_key] = version
            document_id = uuid.uuid4()
            persisted = storage.persist(
                staged, project_id=project_id, document_id=document_id, version=version
            )
            persisted_files.append(persisted)

            document = Document(
                id=document_id,
                original_filename=staged.original_filename,
                source_path=staged.source_path,
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


async def _latest_project_documents(
    session: AsyncSession, project_id: uuid.UUID
) -> list[Document]:
    documents = (
        await session.scalars(
            select(Document)
            .join(ProjectDocument, ProjectDocument.document_id == Document.id)
            .where(ProjectDocument.project_id == project_id)
            .distinct(Document.source_path)
            .order_by(
                Document.source_path,
                Document.version.desc(),
                Document.created_at.desc(),
            )
        )
    ).all()
    return sorted(documents, key=lambda item: (item.created_at, item.source_path))


@router.post(
    "/api/v1/projects/{project_id}/analysis-runs",
    response_model=AnalysisRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["analysis"],
)
async def start_project_analysis(
    project_id: uuid.UUID,
    session: SessionDependency,
    payload: AnalysisRunCreate | None = None,
) -> AnalysisRunAccepted:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    options = payload or AnalysisRunCreate()
    run = await _queue_project_analysis(session, project_id)
    run.force_reextract = options.force_reextract
    run.question_policy = options.question_policy
    await session.commit()
    await session.refresh(run)
    return _analysis_accepted(run)


def _analysis_run_response(
    run: AnalysisRun, result: ProjectAnalysis | None
) -> AnalysisRunResponse:
    result_payload = None
    if result is not None:
        result_payload = {
            "id": result.id,
            "project_type_code": result.project_type_code,
            "confidence": result.confidence,
            "summary": result.summary,
            "rationale": result.rationale,
            "facts": result.facts,
            "assumptions": result.assumptions,
            "issues": result.issues,
            "gaps": result.gaps,
            "questions": result.questions,
            "warnings": result.warnings,
            "stage_signals": result.raw_result.get("stage_signals", []),
            "stage_plan": result.raw_result.get("stage_plan"),
            "document_digests": result.document_digests,
            "source_document_ids": result.source_document_ids,
            "model_name": result.model_name,
            "prompt_version": result.prompt_version,
            "created_at": result.created_at,
        }
    return AnalysisRunResponse(
        run_id=run.id,
        project_id=run.project_id,
        status=run.status,
        current_step=run.current_step,
        document_ids=[uuid.UUID(item) for item in run.input_document_ids],
        errors=run.errors,
        result=result_payload,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.get(
    "/api/v1/projects/{project_id}/analysis-runs/{run_id}",
    response_model=AnalysisRunResponse,
    tags=["analysis"],
)
async def get_project_analysis_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDependency,
) -> AnalysisRunResponse:
    run = await session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.id == run_id, AnalysisRun.project_id == project_id
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    result = await session.scalar(
        select(ProjectAnalysis).where(ProjectAnalysis.run_id == run.id)
    )
    return _analysis_run_response(run, result)


@router.get(
    "/api/v1/projects/{project_id}/analyses/latest",
    response_model=AnalysisRunResponse,
    tags=["analysis"],
)
async def get_latest_project_analysis(
    project_id: uuid.UUID,
    session: SessionDependency,
) -> AnalysisRunResponse:
    run = await session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project_id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Project analysis not found")
    result = await session.scalar(
        select(ProjectAnalysis).where(ProjectAnalysis.run_id == run.id)
    )
    return _analysis_run_response(run, result)


@router.post(
    "/api/v1/projects/{project_id}/analysis-runs/{run_id}/answers",
    response_model=ChatMessageAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["analysis"],
)
async def answer_analysis_questions(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: QuestionAnswerCreate,
    session: SessionDependency,
) -> ChatMessageAccepted:
    project = await session.get(Project, project_id)
    run = await session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.id == run_id,
            AnalysisRun.project_id == project_id,
        )
    )
    if project is None or run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    if run.status != "requires_input":
        raise HTTPException(
            status_code=409,
            detail="Answers are accepted only when analysis requires input",
        )
    message = await _append_chat_message(
        session, project, payload.content, kind="answer"
    )
    next_run = await _queue_project_analysis(session, project_id)
    await session.commit()
    await session.refresh(message)
    return ChatMessageAccepted(
        message=ChatMessageResponse.model_validate(message),
        analysis=_analysis_accepted(next_run),
    )


@router.post(
    "/api/v1/projects/{project_id}/analysis-runs/{run_id}/questions/skip",
    response_model=AnalysisRunResponse,
    tags=["analysis"],
)
async def skip_analysis_questions(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDependency,
) -> AnalysisRunResponse:
    run = await session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.id == run_id,
            AnalysisRun.project_id == project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    if run.status != "requires_input":
        raise HTTPException(
            status_code=409,
            detail="Questions can be skipped only when analysis requires input",
        )
    result = await session.scalar(
        select(ProjectAnalysis).where(ProjectAnalysis.run_id == run.id)
    )
    if result is None:
        raise HTTPException(status_code=409, detail="Analysis result is not ready")
    run.status = "ready"
    run.current_step = "questions_skipped"
    project = await session.get(Project, project_id)
    if project is not None:
        project.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)
    return _analysis_run_response(run, result)


def _pdf_lines(result: ProjectAnalysis) -> list[tuple[str, int]]:
    lines: list[tuple[str, int]] = [
        ("Результат анализа проекта", 20),
        (f"Тип проекта: {result.project_type_code or 'не определён'}", 13),
        (f"Уверенность: {result.confidence}", 11),
        ("", 8),
        ("Резюме", 15),
        (result.summary, 10),
        ("", 8),
        ("Обоснование", 15),
        (result.rationale, 10),
    ]
    sections = [
        ("Факты", [f"{item.get('name')}: {item.get('value')}" for item in result.facts]),
        ("Допущения", result.assumptions),
        ("Проблемы", [item.get("description", "") for item in result.issues]),
        ("Вопросы", [item.get("question", "") for item in result.questions]),
        ("Предупреждения", result.warnings),
    ]
    for title, items in sections:
        if not items:
            continue
        lines.extend([("", 8), (title, 15)])
        lines.extend((f"• {item}", 10) for item in items)
    return lines


def _render_analysis_pdf(result: ProjectAnalysis) -> bytes:
    try:
        import pymupdf
    except ImportError as error:  # pragma: no cover - installed in the Docker image
        raise HTTPException(status_code=503, detail="PDF renderer is unavailable") from error

    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    font_name = "dejavu" if font_path.exists() else "helv"
    if font_path.exists():
        page.insert_font(fontname=font_name, fontfile=str(font_path))
    y = 48.0

    for text, size in _pdf_lines(result):
        wrapped = textwrap.wrap(str(text), width=max(45, int(95 * 10 / size))) or [""]
        for line in wrapped:
            if y > 800:
                page = document.new_page(width=595, height=842)
                if font_path.exists():
                    page.insert_font(fontname=font_name, fontfile=str(font_path))
                y = 48.0
            page.insert_text((48, y), line, fontname=font_name, fontsize=size)
            y += size * 1.45
        y += 3
    return document.tobytes(garbage=4, deflate=True)


@router.get(
    "/api/v1/projects/{project_id}/analysis-runs/{run_id}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    tags=["analysis"],
)
async def download_analysis_pdf(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDependency,
) -> Response:
    run = await session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.id == run_id,
            AnalysisRun.project_id == project_id,
            AnalysisRun.status.in_(["ready", "requires_input"]),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Completed analysis run not found")
    result = await session.scalar(
        select(ProjectAnalysis).where(ProjectAnalysis.run_id == run.id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis result not found")
    return Response(
        content=_render_analysis_pdf(result),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="project-analysis.pdf"'},
    )
