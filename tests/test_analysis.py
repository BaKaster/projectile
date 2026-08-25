from __future__ import annotations

import uuid
import zipfile
from pathlib import Path

import pytest

from app.analysis_contracts import DataGap, material_questions
from app.analyzer import CodexProjectAnalyzer, SourceText, _source_role
from app.recognition import DocumentRecognizer, UnsafeArchiveError


def _recognizer() -> DocumentRecognizer:
    return DocumentRecognizer(
        whisper_model="tiny",
        whisper_device="cpu",
        whisper_compute_type="int8",
        archive_max_files=10,
        archive_max_uncompressed_bytes=1024,
    )


def test_material_questions_returns_every_explicit_question() -> None:
    questions = material_questions(
        [
            DataGap(
                code="users",
                description="Количество пользователей меняет объем поддержки",
                impact="high",
                changes_estimate=True,
                blocking=True,
                can_use_assumption=False,
                question="Сколько пользователей нужно поддерживать?",
            ),
            DataGap(
                code="color",
                description="Не указан цвет интерфейса",
                impact="low",
                changes_estimate=False,
                blocking=False,
                can_use_assumption=False,
                question="Какой нужен цвет?",
            ),
            DataGap(
                code="retention",
                description="Срок хранения можно принять типовым",
                impact="high",
                changes_estimate=True,
                blocking=False,
                can_use_assumption=True,
                suggested_assumption="Хранить один год",
                question="Как долго хранить данные?",
            ),
        ]
    )
    assert [item.code for item in questions] == ["users", "color", "retention"]


def test_plain_russian_text_is_recognized(tmp_path: Path) -> None:
    path = tmp_path / "brief.txt"
    path.write_text("Техническое задание на поддержку пользователей", encoding="utf-8")
    result = _recognizer()._recognize_sync(path, 0)
    assert "поддержку пользователей" in result.text
    assert result.metadata["extractor"] == "text"


def test_digital_pdf_uses_fast_text_layer(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "requirements.pdf"
    document = pymupdf.open()
    for page_number in range(2):
        page = document.new_page()
        for line_number in range(10):
            page.insert_text(
                (72, 72 + line_number * 20),
                "Project requirements and integration constraints "
                f"page={page_number} line={line_number}",
            )
    document.save(path)
    document.close()

    result = _recognizer()._recognize_sync(path, 0)
    assert result.metadata["extractor"] == "pymupdf-text"
    assert result.metadata["page_count"] == 2


def test_docx_uses_fast_native_extractor(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    path = tmp_path / "requirements.docx"
    document = docx.Document()
    document.add_heading("Технические требования", level=1)
    document.add_paragraph("Нужна интеграция с CRM и миграция данных.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Пользователи"
    table.rows[0].cells[1].text = "500"
    document.save(path)

    result = _recognizer()._recognize_sync(path, 0)
    assert result.metadata["extractor"] == "python-docx"
    assert "интеграция с CRM" in result.text
    assert result.tables[0]["rows"] == [["Пользователи", "500"]]


def test_pptx_uses_fast_native_extractor(tmp_path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    path = tmp_path / "brief.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Проект внедрения"
    slide.placeholders[1].text = "Интеграция, миграция и обучение пользователей"
    presentation.save(path)

    result = _recognizer()._recognize_sync(path, 0)
    assert result.metadata["extractor"] == "python-pptx"
    assert "обучение пользователей" in result.text


def test_zip_is_recognized_and_path_traversal_is_rejected(tmp_path: Path) -> None:
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("folder/brief.txt", "Описание проекта")
    result = _recognizer()._recognize_sync(good, 0)
    assert "Описание проекта" in result.text

    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../outside.txt", "no")
    with pytest.raises(UnsafeArchiveError):
        _recognizer()._recognize_sync(unsafe, 0)


def test_model_input_has_document_ids_and_is_bounded() -> None:
    analyzer = CodexProjectAnalyzer("test-model", 10_100)
    document_id = str(uuid.uuid4())
    prompt = analyzer._build_input(
        [{"code": "SUP_L1", "name": "Поддержка"}],
        [SourceText(document_id=document_id, filename="ТЗ.txt", text="x" * 20_000)],
    )
    assert document_id in prompt
    assert len(prompt) <= 10_100
    assert 'role="customer_requirements"' in prompt


def test_large_single_document_skips_redundant_digest_pass() -> None:
    analyzer = CodexProjectAnalyzer("test-model", 10_100)
    sources = [
        SourceText(
            document_id=str(uuid.uuid4()),
            filename="brief.txt",
            text="x" * 50_000,
        )
    ]

    assert analyzer._should_digest_sources(100, sources) is False


def test_large_document_set_still_uses_parallel_digest_pass() -> None:
    analyzer = CodexProjectAnalyzer("test-model", 10_100)
    sources = [
        SourceText(
            document_id=str(uuid.uuid4()),
            filename=f"brief-{index}.txt",
            text="x" * 3_000,
        )
        for index in range(5)
    ]

    assert analyzer._should_digest_sources(100, sources) is True


def test_generated_excel_has_distinct_source_role() -> None:
    assert _source_role("generated/current-estimate.xlsx") == "generated_estimate"


def test_spreadsheet_formula_errors_are_preserved(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "estimate.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Расчёт"
    sheet["A1"] = "Итого"
    sheet["B1"] = "=#REF!+10"
    workbook.save(path)

    result = _recognizer()._recognize_sync(path, 0)
    assert "=#REF!+10" in result.text
    assert result.metadata["formula_errors"][0]["cell"] == "B1"
