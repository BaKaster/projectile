from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.analysis_contracts import material_questions
from app.analyzer import (
    PROMPT_VERSION,
    OpenAIProjectAnalyzer,
    SourceText,
    catalog_for_prompt,
)
from app.config import Settings
from app.models import (
    AnalysisRun,
    Document,
    DocumentExtraction,
    ProjectAnalysis,
    ProjectType,
)
from app.recognition import (
    DocumentRecognizer,
    UnsupportedFormatError,
    metadata_as_table,
)
from app.stage_contracts import StagePlanContext
from app.stage_planner import StagePlanner
from app.storage import LocalFileStorage

logger = logging.getLogger(__name__)


class AnalysisWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        stage_planner: StagePlanner,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.stage_planner = stage_planner
        self.storage = LocalFileStorage(
            settings.storage_root,
            settings.max_upload_size_bytes,
            settings.upload_chunk_size_bytes,
        )
        self.recognizer = DocumentRecognizer(
            whisper_model=settings.recognition_model,
            whisper_device=settings.recognition_device,
            whisper_compute_type=settings.recognition_compute_type,
            archive_max_files=settings.archive_max_files,
            archive_max_uncompressed_bytes=settings.archive_max_uncompressed_bytes,
        )
        self._task: asyncio.Task | None = None

    async def recover_interrupted(self) -> None:
        async with self.session_factory.begin() as session:
            await session.execute(
                update(AnalysisRun)
                .where(AnalysisRun.status.in_(["extracting", "analyzing"]))
                .values(status="queued", current_step="recovered_after_restart")
            )

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="project-analysis-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _loop(self) -> None:
        while True:
            try:
                processed = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - keep the worker alive
                logger.error("Analysis worker polling failed: %s", type(error).__name__)
                processed = False
            if not processed:
                await asyncio.sleep(self.settings.analysis_poll_interval_seconds)

    async def _claim(self) -> uuid.UUID | None:
        async with self.session_factory.begin() as session:
            run = await session.scalar(
                select(AnalysisRun)
                .where(AnalysisRun.status == "queued")
                .order_by(AnalysisRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            run.status = "extracting"
            run.current_step = "extracting_documents"
            return run.id

    async def process_once(self) -> bool:
        run_id = await self._claim()
        if run_id is None:
            return False
        try:
            await self._process(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - persist terminal run failures
            logger.error("Analysis run %s failed: %s", run_id, type(error).__name__)
            await self._fail(run_id, error)
        return True

    async def _process(self, run_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            run = await session.get(AnalysisRun, run_id)
            if run is None:
                return
            ids = [uuid.UUID(item) for item in run.input_document_ids]
            rows = await session.scalars(select(Document).where(Document.id.in_(ids)))
            by_id = {row.id: row for row in rows.all()}
            documents = [by_id[item] for item in ids if item in by_id]

        sources: list[SourceText] = []
        warnings: list[str] = []
        for document in documents:
            extraction = await self._extract(document, force=run.force_reextract)
            if extraction.status == "ready" and extraction.extracted_text:
                sources.append(
                    SourceText(
                        document_id=str(document.id),
                        filename=document.source_path,
                        text=extraction.extracted_text,
                    )
                )
            elif extraction.errors:
                warnings.extend(str(item.get("message", item)) for item in extraction.errors)

        if not sources:
            raise RuntimeError("Ни из одного файла не удалось получить текст")
        if self.settings.openai_api_key is None:
            raise RuntimeError(
                "Не задан KEY_OPENAI, OPENAI_API_KEY или PROJECTILE_OPENAI_API_KEY"
            )

        async with self.session_factory.begin() as session:
            run = await session.get(AnalysisRun, run_id, with_for_update=True)
            if run is None:
                return
            run.status = "analyzing"
            run.current_step = "classifying_and_finding_gaps"
            catalog_rows = (await session.scalars(select(ProjectType))).all()

        analyzer = OpenAIProjectAnalyzer(
            api_key=self.settings.openai_api_key.get_secret_value(),
            model=self.settings.analysis_model,
            max_input_characters=self.settings.analysis_max_input_characters,
            digest_concurrency=self.settings.analysis_digest_concurrency,
        )
        analyzer_output = await analyzer.analyze(catalog_for_prompt(catalog_rows), sources)
        result = analyzer_output.result
        allowed_codes = {row.code for row in catalog_rows}
        if result.project_type_code not in allowed_codes:
            result.project_type_code = None
            result.confidence = "low"
            result.warnings.append("Модель не смогла однозначно выбрать тип из каталога")

        questions = material_questions(result.gaps)
        needs_input = any(question.blocking for question in questions)
        if warnings:
            result.warnings.extend(warnings)
        raw_result = result.model_dump(mode="json")
        if result.project_type_code is not None:
            allowed_signals = {
                item.code for item in self.stage_planner.catalog.signal_catalog
            }
            stage_signals = sorted(
                {
                    item.code
                    for item in result.stage_signals
                    if item.code in allowed_signals
                }
            )
            ignored_signals = sorted(
                {
                    item.code
                    for item in result.stage_signals
                    if item.code not in allowed_signals
                }
            )
            if ignored_signals:
                result.warnings.append(
                    "Проигнорированы неизвестные сигналы этапов: "
                    + ", ".join(ignored_signals)
                )
            stage_plan = self.stage_planner.build_plan(
                result.project_type_code,
                StagePlanContext(signals=stage_signals),
            )
            raw_result = result.model_dump(mode="json")
            raw_result["stage_plan"] = stage_plan.model_dump(mode="json")

        async with self.session_factory.begin() as session:
            run = await session.get(AnalysisRun, run_id, with_for_update=True)
            if run is None:
                return
            session.add(
                ProjectAnalysis(
                    run_id=run.id,
                    project_id=run.project_id,
                    project_type_code=result.project_type_code,
                    confidence=result.confidence,
                    summary=result.summary,
                    rationale=result.rationale,
                    facts=[item.model_dump(mode="json") for item in result.facts],
                    assumptions=result.assumptions,
                    issues=[item.model_dump(mode="json") for item in result.issues],
                    gaps=[item.model_dump(mode="json") for item in result.gaps],
                    questions=[item.model_dump(mode="json") for item in questions],
                    warnings=result.warnings,
                    document_digests=[
                        item.model_dump(mode="json")
                        for item in analyzer_output.document_digests
                    ],
                    source_document_ids=run.input_document_ids,
                    raw_result=raw_result,
                    model_name=self.settings.analysis_model,
                    prompt_version=PROMPT_VERSION,
                )
            )
            run.status = "requires_input" if needs_input else "ready"
            run.current_step = "waiting_for_material_answers" if needs_input else "completed"
            run.model_name = self.settings.analysis_model

    async def _extract(
        self, document: Document, *, force: bool
    ) -> DocumentExtraction:
        async with self.session_factory.begin() as session:
            extraction = await session.scalar(
                select(DocumentExtraction)
                .where(DocumentExtraction.document_id == document.id)
                .with_for_update()
            )
            if extraction is None:
                extraction = DocumentExtraction(document_id=document.id, status="pending")
                session.add(extraction)
                await session.flush()
            if extraction.status == "ready" and not force:
                return extraction
            extraction.status = "extracting"
            extraction.errors = []

        try:
            result = await self.recognizer.recognize(
                self.storage.resolve_uri(document.storage_uri)
            )
            status = "ready" if result.text.strip() else "unsupported"
            error_rows = [{"message": item} for item in result.warnings]
            tables = metadata_as_table(result)
            text = result.text
        except UnsupportedFormatError as error:
            status = "unsupported"
            error_rows = [{"type": type(error).__name__, "message": str(error)}]
            tables = []
            text = None
        except Exception as error:  # noqa: BLE001 - isolate one bad document
            status = "failed"
            error_rows = [{"type": type(error).__name__, "message": str(error)[:2000]}]
            tables = []
            text = None

        async with self.session_factory.begin() as session:
            extraction = await session.scalar(
                select(DocumentExtraction)
                .where(DocumentExtraction.document_id == document.id)
                .with_for_update()
            )
            if extraction is None:
                raise RuntimeError("Запись распознавания исчезла")
            extraction.status = status
            extraction.extractor_version = self.recognizer.VERSION
            extraction.extracted_text = text
            extraction.tables = tables
            extraction.errors = error_rows
            return extraction

    async def _fail(self, run_id: uuid.UUID, error: Exception) -> None:
        async with self.session_factory.begin() as session:
            run = await session.get(AnalysisRun, run_id, with_for_update=True)
            if run is None:
                return
            run.status = "failed"
            run.current_step = "failed"
            run.errors = [
                {"type": type(error).__name__, "message": str(error)[:2000]}
            ]
