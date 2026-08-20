from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

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
from app.excel_estimate import (
    ExcelEstimateError,
    ExcelEstimateRequest,
    ExcelEstimateService,
    TypeParameterDefinition,
)
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
    ChatUpdate,
    DocumentUploadResponse,
    HealthResponse,
    ProjectCreate,
    ProjectResponse,
    QuestionAnswerCreate,
    StagePlanRequest,
    UploadedDocumentResponse,
    WorkPlanRequest,
)
from app.stage_contracts import ProjectStagePlan
from app.stage_planner import StagePlanningError
from app.storage import (
    FileTooLargeError,
    LocalFileStorage,
    PersistedFile,
    StagedUpload,
    safe_filename,
)
from app.work_contracts import GeneratedWorkPlan
from app.work_generator import WorkGenerationError

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


@router.post(
    "/api/v1/project-types/{project_type_code}/work-plan",
    response_model=GeneratedWorkPlan,
    tags=["works"],
)
async def build_project_work_plan(
    project_type_code: str,
    payload: WorkPlanRequest,
    request: Request,
) -> GeneratedWorkPlan:
    try:
        stage_plan = request.app.state.stage_planner.build_plan(
            project_type_code, payload.stage_context
        )
        work_plan = request.app.state.work_generator.generate(
            stage_plan, payload.work_context
        )
        effort_estimator = getattr(request.app.state, "effort_estimator", None)
        settings = getattr(request.app.state, "settings", None)
        if effort_estimator is None:
            return work_plan
        if (
            payload.effort_mode == "auto"
            and settings is not None
            and settings.openai_api_key is not None
        ):
            try:
                return await effort_estimator.plan_with_ai(
                    work_plan,
                    api_key=settings.openai_api_key.get_secret_value(),
                    model=settings.analysis_model,
                    reasoning_effort=settings.analysis_reasoning_effort,
                )
            except Exception:  # noqa: BLE001 - return a useful deterministic plan
                estimated = effort_estimator.estimate(work_plan)
                estimated.warnings.append(
                    "Не удалось уточнить трудозатраты моделью; применена детерминированная оценка."
                )
                return estimated
        return effort_estimator.estimate(work_plan)
    except StagePlanningError as error:
        status_code = 404 if str(error).startswith("unknown project type") else 422
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except WorkGenerationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/api/v1/project-types/{project_type_code}/estimate-parameters",
    response_model=list[TypeParameterDefinition],
    tags=["estimates"],
)
async def get_estimate_parameters(
    project_type_code: str,
    request: Request,
) -> list[TypeParameterDefinition]:
    try:
        return request.app.state.excel_estimate_service.parameter_definitions(
            project_type_code
        )
    except ExcelEstimateError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/api/v1/estimates/workbook",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
            "description": "A populated MONSters estimate workbook",
        }
    },
    tags=["estimates"],
)
async def build_estimate_workbook(
    payload: ExcelEstimateRequest,
    request: Request,
) -> Response:
    try:
        service = request.app.state.excel_estimate_service
        content = service.build(payload)
    except ExcelEstimateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", payload.project_name).strip("-_")
    ascii_name = f"{safe_name or 'estimate'}.xlsx"
    unicode_name = quote(f"{payload.project_name}.xlsx")
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{unicode_name}'
            ),
            "X-Excel-Recalculation": service.recalculation_status,
        },
    )


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
    project = Project(
        id=payload.id or uuid.uuid4(),
        name=payload.name.strip(),
        name_is_generated=False,
    )
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
    session: AsyncSession, project_id: uuid.UUID, *, finalize_without_questions: bool = False
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
        question_policy=("final_after_answers" if finalize_without_questions else "material_only"),
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
    project.updated_at = datetime.now(UTC)
    await session.flush()
    return message


@router.post(
    "/api/v1/chats",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["chats"],
)
async def create_chat(payload: ChatCreate, session: SessionDependency) -> Project:
    supplied_name = (payload.name or "").strip()
    project = Project(
        name=supplied_name or "Новый чат",
        name_is_generated=not bool(supplied_name),
    )
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


@router.patch(
    "/api/v1/chats/{chat_id}",
    response_model=ProjectResponse,
    tags=["chats"],
)
async def update_chat(
    chat_id: uuid.UUID,
    payload: ChatUpdate,
    session: SessionDependency,
) -> Project:
    project = await session.get(Project, chat_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Chat name cannot be empty")
    project.name = name
    project.name_is_generated = False
    project.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete(
    "/api/v1/chats/{chat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["chats"],
)
async def delete_chat(chat_id: uuid.UUID, session: SessionDependency) -> Response:
    project = await session.get(Project, chat_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    await session.delete(project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
            "work_plan": result.raw_result.get("work_plan"),
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
    # The clarification loop is intentionally single-pass: the response is
    # incorporated into a final estimate, never used to ask a second round.
    next_run = await _queue_project_analysis(
        session, project_id, finalize_without_questions=True
    )
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
        project.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(run)
    return _analysis_run_response(run, result)


def _wrap_pdf_text(text: str, font: object, size: float, width: float) -> list[str]:
    """Wrap text using the embedded font metrics so Russian text stays aligned."""
    wrapped: list[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        words = paragraph.split()
        if not words:
            wrapped.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if font.text_length(candidate, fontsize=size) <= width:
                line = candidate
                continue
            if line:
                wrapped.append(line)
                line = ""
            while font.text_length(word, fontsize=size) > width:
                split_at = max(1, len(word) - 1)
                while split_at > 1 and font.text_length(word[:split_at], fontsize=size) > width:
                    split_at -= 1
                wrapped.append(word[:split_at])
                word = word[split_at:]
            line = word
        if line:
            wrapped.append(line)
    return wrapped or [""]


def _render_analysis_pdf(
    result: ProjectAnalysis,
    run_id: uuid.UUID,
    theme: Literal["light", "dark"] = "light",
) -> bytes:
    try:
        import pymupdf
    except ImportError as error:  # pragma: no cover - installed in the Docker image
        raise HTTPException(status_code=503, detail="PDF renderer is unavailable") from error

    regular_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    document = pymupdf.open()
    page_width, page_height = 595.0, 842.0
    margin, content_width, bottom = 44.0, 507.0, 798.0
    if theme == "dark":
        page_background = (0.075, 0.075, 0.075)
        black, white = (0.96, 0.96, 0.96), (0.075, 0.075, 0.075)
        muted, soft = (0.76, 0.76, 0.76), (0.145, 0.145, 0.145)
        line_color, outline, outlined_fill = (
            (0.27, 0.27, 0.27),
            (0.42, 0.42, 0.42),
            (0.105, 0.105, 0.105),
        )
    else:
        page_background = (0.985, 0.985, 0.98)
        black, white = (0.07, 0.07, 0.07), (1.0, 1.0, 1.0)
        muted, soft = (0.34, 0.34, 0.34), (0.94, 0.94, 0.93)
        line_color, outline, outlined_fill = (
            (0.82, 0.82, 0.81),
            (0.58, 0.58, 0.57),
            (0.995, 0.995, 0.99),
        )
    regular_name = "dejavu" if regular_path.exists() else "helv"
    bold_name = "dejavu-bold" if bold_path.exists() else "helv-bold"
    regular_font = pymupdf.Font(fontfile=str(regular_path)) if regular_path.exists() else pymupdf.Font("helv")
    bold_font = pymupdf.Font(fontfile=str(bold_path)) if bold_path.exists() else pymupdf.Font("hebo")
    page = None
    y = margin

    def new_page() -> None:
        nonlocal page, y
        page = document.new_page(width=page_width, height=page_height)
        page.draw_rect(
            pymupdf.Rect(0, 0, page_width, page_height),
            fill=page_background,
            color=page_background,
            overlay=False,
        )
        if regular_path.exists():
            page.insert_font(fontname=regular_name, fontfile=str(regular_path))
        if bold_path.exists():
            page.insert_font(fontname=bold_name, fontfile=str(bold_path))
        page.draw_rect(pymupdf.Rect(margin, 30, margin + 24, 54), fill=black, color=black, radius=.3)
        page.insert_text((margin + 8.2, 47.2), "P", fontname=bold_name, fontsize=10, color=white)
        page.insert_text((margin + 34, 46.5), "PROJECTILE", fontname=bold_name, fontsize=9, color=black)
        page.insert_text((page_width - margin - 94, 46.5), f"АНАЛИЗ {str(run_id)[:8].upper()}", fontname=regular_name, fontsize=6.5, color=muted)
        page.draw_line((margin, 65), (page_width - margin, 65), color=line_color, width=.7)
        page_number = len(document)
        page.insert_text((margin, 817), f"Projectile  •  Анализ проекта  •  {page_number}", fontname=regular_name, fontsize=6.5, color=muted)
        y = 88.0

    def ensure_space(height: float) -> None:
        if y + height > bottom:
            new_page()

    def draw_lines(
        text: str,
        *,
        size: float = 9.5,
        bold: bool = False,
        color: tuple[float, float, float] = black,
        width: float = content_width,
        x: float = margin,
        line_height: float | None = None,
    ) -> None:
        nonlocal y
        font = bold_font if bold else regular_font
        font_name = bold_name if bold else regular_name
        leading = line_height or size * 1.48
        for line in _wrap_pdf_text(text, font, size, width):
            ensure_space(leading)
            page.insert_text((x, y + size), line, fontname=font_name, fontsize=size, color=color)
            y += leading

    def section(title: str) -> None:
        nonlocal y
        ensure_space(34)
        y += 13
        draw_lines(title, size=12, bold=True)
        y += 7

    def paragraph(text: str) -> None:
        nonlocal y
        draw_lines(text, size=10.5, color=muted, line_height=15.5)
        y += 4

    def card(title: str, text: str, *, outlined: bool = False) -> None:
        nonlocal y
        padding = 12.0
        title_lines = _wrap_pdf_text(title, bold_font, 9.5, content_width - padding * 2)
        body_lines = _wrap_pdf_text(text, regular_font, 9.7, content_width - padding * 2)
        line_height = 14.0
        all_rows: list[tuple[str, bool, tuple[float, float, float]]] = [
            *((item, True, black) for item in title_lines),
            *((item, False, muted) for item in body_lines),
        ]
        max_rows = max(2, int((bottom - 88 - padding * 2) / line_height))
        for start in range(0, len(all_rows), max_rows):
            rows = all_rows[start:start + max_rows]
            height = padding * 2 + len(rows) * line_height + (3 if start == 0 else 0)
            ensure_space(height + 7)
            rect = pymupdf.Rect(margin, y, page_width - margin, y + height)
            page.draw_rect(
                rect,
                color=outline if outlined else soft,
                fill=outlined_fill if outlined else soft,
                width=.8,
                radius=.16,
            )
            cursor = y + padding
            for row, is_bold, row_color in rows:
                page.insert_text(
                    (margin + padding, cursor + 8.5),
                    row,
                    fontname=bold_name if is_bold else regular_name,
                    fontsize=9.5 if is_bold else 9.7,
                    color=row_color,
                )
                cursor += line_height
                if is_bold and start == 0:
                    cursor += 3
            y += height + 7

    def bullet_list(items: list[str]) -> None:
        nonlocal y
        if not items:
            paragraph("Нет существенных пунктов.")
            return
        for item in items:
            ensure_space(18)
            page.draw_rect(pymupdf.Rect(margin, y + 4, margin + 5, y + 9), fill=black, color=black, radius=.3)
            draw_lines(str(item), size=10, color=muted, width=content_width - 17, x=margin + 17, line_height=15)
            y += 5

    new_page()
    draw_lines("РЕЗУЛЬТАТ АНАЛИЗА", size=7, bold=True, color=muted)
    y += 3
    draw_lines(result.project_type_code or "Тип проекта не определён", size=20, bold=True, line_height=26)
    y += 6
    confidence = {"low": "НИЗКАЯ", "medium": "СРЕДНЯЯ", "high": "ВЫСОКАЯ"}.get(result.confidence, result.confidence.upper())
    label_width = bold_font.text_length(f"УВЕРЕННОСТЬ: {confidence}", fontsize=7) + 22
    page.draw_rect(pymupdf.Rect(margin, y, margin + label_width, y + 22), fill=black, color=black, radius=.5)
    page.insert_text((margin + 11, y + 14.3), f"УВЕРЕННОСТЬ: {confidence}", fontname=bold_name, fontsize=7, color=white)
    y += 34
    card("Резюме", result.summary)

    section("Обоснование")
    paragraph(result.rationale)
    section("Факты")
    if result.facts:
        for item in result.facts:
            card(str(item.get("name") or "Факт"), str(item.get("value") or ""))
    else:
        paragraph("Нет существенных пунктов.")
    section("Допущения")
    bullet_list(result.assumptions)
    section("Проблемы и риски")
    if result.issues:
        for item in result.issues:
            card(str(item.get("description") or "Проблема"), str(item.get("impact_on_estimate") or ""))
    else:
        paragraph("Нет существенных пунктов.")
    section("Вопросы")
    if result.questions:
        for item in result.questions:
            card(str(item.get("question") or "Вопрос"), str(item.get("reason") or ""), outlined=True)
    else:
        paragraph("Нет существенных пунктов.")
    section("Предупреждения")
    bullet_list(result.warnings)
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
    theme: Literal["light", "dark"] = "light",
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
        content=_render_analysis_pdf(result, run.id, theme),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="projectile-analysis-{run.id}-{theme}.pdf"'
            )
        },
    )


def _analysis_estimate_payload(
    project: Project,
    result: ProjectAnalysis,
    run: AnalysisRun,
    estimate_service: ExcelEstimateService | None = None,
) -> ExcelEstimateRequest:
    project_type_code = result.project_type_code
    if project_type_code is None:
        # A final estimate is a delivery guarantee.  The neutral complex IT
        # profile is deliberately conservative and the uncertainty is surfaced
        # in the workbook instead of returning a dead-end HTTP error.
        project_type_code = "SUP_Complex"
    stage_plan = result.raw_result.get("stage_plan")
    work_plan = result.raw_result.get("work_plan")
    if not isinstance(stage_plan, dict) or not isinstance(work_plan, dict):
        stage_plan = {"stages": [{"code": "fallback", "name": "Предварительная оценка", "order": 1, "status": "selected"}]}
        work_plan = {
            "packages": [{"stage_code": "fallback", "works": [{
                "work_code": "fallback.discovery",
                "name": "Предварительный анализ и формирование оценки",
                "role_code": "project_manager",
                "effort_hours": 8,
                "hours_basis": "Всего",
                "selection_reason": "Резервный состав работ при неполных исходных данных",
            }]}]
        }

    stages = {
        item.get("code"): item
        for item in stage_plan.get("stages", [])
        if isinstance(item, dict) and item.get("status") == "selected"
    }
    stage_numbers = {
        item["code"]: index
        for index, item in enumerate(
            sorted(stages.values(), key=lambda value: value.get("order", 999)), 1
        )
    }
    work_items = []
    for package in work_plan.get("packages", []):
        if not isinstance(package, dict):
            continue
        stage_code = package.get("stage_code")
        stage = stages.get(stage_code, {})
        stage_no = stage_numbers.get(stage_code, len(stage_numbers) + 1)
        stage_name = stage.get("name") or stage_code or f"Этап {stage_no}"
        for work_index, work in enumerate(package.get("works", []), 1):
            if not isinstance(work, dict):
                continue
            assignments = [
                {
                    "role": assignment.get("role_code"),
                    "estimated_hours": assignment.get("effort_hours"),
                    "responsibility": assignment.get("responsibility"),
                }
                for assignment in work.get("role_assignments", [])
                if isinstance(assignment, dict)
                and assignment.get("role_code")
                and assignment.get("effort_hours")
            ]
            if not assignments and work.get("role_code") and work.get("effort_hours"):
                assignments = [
                    {
                        "role": work["role_code"],
                        "estimated_hours": work["effort_hours"],
                    }
                ]
            if not assignments:
                assignments = [{"role": "project_manager", "estimated_hours": 8}]
            comment_parts = [work.get("selection_reason")]
            comment_parts.extend(work.get("assumptions") or [])
            work_items.append(
                {
                    "stage_no": stage_no,
                    "stage_name": stage_name,
                    "work_no": f"{stage_no}.{work_index}",
                    "work_name": work.get("name") or work.get("work_code"),
                    "role_assignments": assignments,
                    "hours_basis": work.get("hours_basis") or "Всего",
                    "comment": "; ".join(
                        str(item).strip() for item in comment_parts if item
                    )[:2000]
                    or None,
                }
            )

    if not work_items:
        work_items = [{
            "stage_no": 1,
            "stage_name": "Предварительная оценка",
            "work_no": "1.1",
            "work_name": "Предварительный анализ и формирование оценки",
            "role_assignments": [{"role": "project_manager", "estimated_hours": 8}],
            "hours_basis": "Всего",
            "comment": "Резервный состав работ: исходные данные не позволили разложить проект детальнее.",
        }]

    # The workbook has exactly 100 calculation rows.  Do not expose that
    # implementation limit to the customer: retain total hours and fold the
    # smallest auxiliary assignments into the primary role of the same work.
    # The resulting loss of role granularity is explicitly disclosed as a risk.
    compacted_assignments = 0
    def expanded_row_count() -> int:
        return sum(len(item["role_assignments"]) for item in work_items)

    while expanded_row_count() > 100:
        candidates = [
            (assignment["estimated_hours"], work_index, assignment_index)
            for work_index, item in enumerate(work_items)
            if len(item["role_assignments"]) > 1
            for assignment_index, assignment in enumerate(item["role_assignments"])
        ]
        if not candidates:
            break
        _hours, work_index, assignment_index = min(candidates)
        assignments = work_items[work_index]["role_assignments"]
        removed = assignments.pop(assignment_index)
        target = max(assignments, key=lambda item: item["estimated_hours"])
        target["estimated_hours"] += removed["estimated_hours"]
        work_items[work_index]["comment"] = (
            (work_items[work_index].get("comment") or "")
            + f"; Трудозатраты роли {removed['role']} объединены с {target['role']} "
            "из-за лимита строк Excel."
        )[:2000]
        compacted_assignments += 1

    if expanded_row_count() > 100:
        # This only applies to exceptional plans with more than 100 independent
        # works.  Preserve the total in the same stage rather than failing the
        # report download.
        while expanded_row_count() > 100 and len(work_items) > 1:
            source_index = min(
                range(len(work_items)),
                key=lambda index: sum(
                    item["estimated_hours"]
                    for item in work_items[index]["role_assignments"]
                ),
            )
            source = work_items.pop(source_index)
            target = next(
                (item for item in work_items if item["stage_no"] == source["stage_no"]),
                work_items[0],
            )
            target_assignment = max(
                target["role_assignments"], key=lambda item: item["estimated_hours"]
            )
            target_assignment["estimated_hours"] += sum(
                item["estimated_hours"] for item in source["role_assignments"]
            )
            target["comment"] = (
                (target.get("comment") or "")
                + f"; Включены трудозатраты укрупнённой работы: {source['work_name']}."
            )[:2000]
            compacted_assignments += len(source["role_assignments"])

    assumptions: list[dict] = []
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for issue in sorted(
        result.issues,
        key=lambda item: severity_rank.get(item.get("severity", "low"), 3),
    ):
        assumptions.append(
            {
                "type": "Риск",
                "text": issue.get("description") or issue.get("code"),
                "source": ", ".join(issue.get("source_document_ids") or []) or None,
                "impact": issue.get("impact_on_estimate"),
            }
        )
    for gap in result.gaps:
        assumptions.append(
            {
                "type": "Открытый вопрос",
                "text": gap.get("question") or gap.get("description") or gap.get("code"),
                "source": None,
                "impact": gap.get("suggested_assumption") or gap.get("impact"),
            }
        )
    assumptions.extend(
        {"type": "Допущение", "text": item, "source": None, "impact": None}
        for item in result.assumptions
    )
    assumptions.extend(
        {"type": "Риск", "text": item, "source": None, "impact": "Требует проверки при согласовании оценки"}
        for item in result.warnings
    )
    if compacted_assignments:
        assumptions.insert(0, {
            "type": "Риск",
            "text": "Детализация ролей укрупнена для совместимости с лимитом строк Excel.",
            "source": None,
            "impact": "Общая трудоёмкость сохранена; распределение часов по вспомогательным ролям отражено в комментариях к работам.",
        })
    if result.project_type_code is None:
        assumptions.insert(0, {
            "type": "Риск",
            "text": "Тип проекта не определён однозначно; для выпуска сметы применён консервативный профиль SUP_Complex.",
            "source": None,
            "impact": "Итоговая трудоёмкость и этапы требуют уточнения при появлении исходных данных.",
        })

    confidence = {"low": 0.5, "medium": 0.75, "high": 0.9}[result.confidence]
    explicit_parameters = result.raw_result.get("type_parameters", [])
    type_parameters = (
        estimate_service.infer_type_parameters(
            project_type_code,
            result.facts,
            explicit_parameters,
        )
        if estimate_service is not None
        else explicit_parameters
    )
    if estimate_service is not None:
        occupied_slots = {
            int(item["slot_number"])
            for item in type_parameters
            if item.get("slot_number") is not None
        }
        assumed_parameters: list[str] = []
        safe_values = {
            "TERM_MONTHS": 1,
            "QTY": 1,
            "COMPLEXITY": 1,
            "PARALLEL_FTE": 1,
            "LEAD_DAYS": 0,
            "RISK_PCT": 0,
            "MARKUP": 0,
            "TARGET_MARGIN": 0,
        }
        for definition in estimate_service.parameter_definitions(project_type_code):
            if not definition.required or definition.slot_number in occupied_slots:
                continue
            if definition.default_value not in (None, ""):
                continue
            value = safe_values.get(definition.influence_code, "Не указано")
            type_parameters.append({"slot_number": definition.slot_number, "value": value})
            assumed_parameters.append(definition.parameter_name)
        if assumed_parameters:
            assumptions.insert(0, {
                "type": "Риск",
                "text": "Неуказанные обязательные параметры заполнены нейтральными допущениями: " + ", ".join(assumed_parameters),
                "source": None,
                "impact": "Excel выпущен без блокировки; перед коммерческим согласованием значения необходимо подтвердить.",
            })
    try:
        return ExcelEstimateRequest.model_validate(
            {
                "project_name": project.name,
                "project_type_code": project_type_code,
                "estimate_date": result.created_at.date(),
                "estimate_mode": (
                    "Уточнённая" if result.confidence == "high" else "Бюджетная"
                ),
                "confidence": confidence,
                "vat_rate": 0.2,
                "discount_rate": 0,
                "work_hours_per_day": 8,
                "default_hours_basis": "Всего",
                "source_or_spec_version": (
                    f"analysis_run={run.id}; model={result.model_name}; "
                    f"prompt={result.prompt_version}"
                ),
                "main_assumption": (result.assumptions[0] if result.assumptions else (result.warnings[0] if result.warnings else None)),
                "commercial_reserve_rate": 0,
                "type_parameters": type_parameters,
                "work_items": work_items,
                "external_costs": result.raw_result.get("external_costs", []),
                "assumptions": assumptions[:4],
            }
        )
    except ValueError as error:
        raise ExcelEstimateError(f"analysis cannot populate Excel template: {error}") from error


@router.get(
    "/api/v1/projects/{project_id}/analysis-runs/{run_id}/report.xlsx",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            }
        }
    },
    tags=["analysis"],
)
async def download_analysis_xlsx(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDependency,
    request: Request,
) -> Response:
    run = await session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.id == run_id,
            AnalysisRun.project_id == project_id,
            AnalysisRun.status == "ready",
        )
    )
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Answer the required questions before creating an Excel report",
        )
    result = await session.scalar(
        select(ProjectAnalysis).where(ProjectAnalysis.run_id == run.id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis result not found")
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        service = request.app.state.excel_estimate_service
        payload = _analysis_estimate_payload(project, result, run, service)
        content = service.build(payload)
    except ExcelEstimateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    storage = LocalFileStorage(
        request.app.state.settings.storage_root,
        request.app.state.settings.max_upload_size_bytes,
        request.app.state.settings.upload_chunk_size_bytes,
    )
    artifact_source_path = "generated/current-estimate.xlsx"
    checksum = hashlib.sha256(content).hexdigest()
    previous = await session.scalar(
        select(Document)
        .join(ProjectDocument, ProjectDocument.document_id == Document.id)
        .where(
            ProjectDocument.project_id == project_id,
            Document.source_path == artifact_source_path,
        )
        .order_by(Document.version.desc())
        .limit(1)
    )
    if previous is None or previous.checksum_sha256 != checksum:
        version = 1 if previous is None else previous.version + 1
        document_id = uuid.uuid4()
        stored_filename = safe_filename(f"{project.name}.xlsx")
        persisted = storage.persist_bytes(
            content,
            project_id,
            document_id,
            version,
            stored_filename,
        )
        try:
            session.add_all(
                [
                    Document(
                        id=document_id,
                        original_filename=stored_filename,
                        source_path=artifact_source_path,
                        stored_filename=stored_filename,
                        media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                        size_bytes=len(content),
                        checksum_sha256=checksum,
                        storage_uri=persisted.storage_uri,
                        version=version,
                    ),
                    ProjectDocument(project_id=project_id, document_id=document_id),
                    DocumentExtraction(
                        document_id=document_id,
                        status="pending",
                        tables=[],
                        errors=[],
                    ),
                ]
            )
            await session.commit()
        except Exception:
            storage.remove_persisted(persisted)
            raise
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", project.name).strip("-_")
    ascii_name = f"{safe_name or 'estimate'}.xlsx"
    unicode_name = quote(f"{project.name}.xlsx")
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{unicode_name}'
            ),
            "X-Excel-Recalculation": service.recalculation_status,
        },
    )
