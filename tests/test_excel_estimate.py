from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import (
    _analysis_estimate_payload,
    _explain_compacted_hours,
    _hours_rationale,
    router,
)
from app.excel_estimate import (
    ExcelEstimateError,
    ExcelEstimateRequest,
    ExcelEstimateService,
    _WorkbookPackage,
)
from app.models import AnalysisRun, Project, ProjectAnalysis

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "Шаблон.xlsx"
ROLE_CATALOG = ROOT / "data" / "role-effort-catalog.json"


@pytest.fixture(scope="module")
def service() -> ExcelEstimateService:
    return ExcelEstimateService(TEMPLATE, role_catalog_path=ROLE_CATALOG)


def _payload(**overrides) -> ExcelEstimateRequest:
    data = {
        "project_name": "Миграция платформы",
        "project_type_code": "SUP_IT_Implementation",
        "estimate_date": "2026-08-20",
        "estimate_mode": "Уточнённая",
        "confidence": 0.85,
        "vat_rate": 0.22,
        "discount_rate": 0.05,
        "work_hours_per_day": 8,
        "planned_start_date": "2026-09-01",
        "source_or_spec_version": "ТЗ v3",
        "main_assumption": "Доступы предоставляются до старта",
        "commercial_reserve_rate": 0.1,
        "type_parameters": [
            {"influence_code": "QTY", "value": 12},
            {"influence_code": "COMPLEXITY", "value": 1.2},
            {"influence_code": "RISK_PCT", "value": 0.1},
        ],
        "work_items": [
            {
                "stage_no": 1,
                "stage_name": "Проектирование",
                "work_no": "1.1",
                "work_name": "Спроектировать целевую архитектуру",
                "hours_basis": "Всего",
                "site_or_contour": "Основной контур",
                "comment": "По материалам ТЗ",
                "role_assignments": [
                    {
                        "role": "technical_architect",
                        "estimated_hours": 24,
                        "responsibility": "Архитектура",
                        "hours_rationale": "24 ч определены по объёму проектирования.",
                    },
                    {
                        "role": "windows_l3",
                        "estimated_hours": 8,
                        "responsibility": "Проверка Windows-компонентов",
                        "hours_rationale": "8 ч на проверку зависимостей Windows.",
                    },
                ],
            },
            {
                "stage_no": 2,
                "stage_name": "Внедрение",
                "work_no": 1,
                "work_name": "Выполнить внедрение",
                "role": "#1202",
                "estimated_hours": 40,
                "hours_basis": "На единицу",
                "quantity_override": 3,
            },
        ],
        "stage_explanations": [
            {
                "stage_no": 1,
                "explanation": "Нужен для согласования целевой архитектуры.",
            },
            {
                "stage_no": 2,
                "explanation": "Нужен для внедрения согласованного решения.",
            },
        ],
        "external_costs": [
            {
                "category": "ПО",
                "description": "Подписка на систему мониторинга",
                "quantity": 2,
                "unit": "лицензия",
                "unit_cost": 100000,
                "periodicity": "Разово",
            }
        ],
        "assumptions": [
            {
                "type": "Риск",
                "text": "Срок выдачи доступов не подтверждён",
                "source": "ТЗ v3",
                "impact": "Возможен сдвиг старта",
            }
        ],
    }
    data.update(overrides)
    return ExcelEstimateRequest.model_validate(data)


def _cell_value(package: _WorkbookPackage, sheet: str, cell: str):
    import re

    match = re.fullmatch(r"([A-Z]+)(\d+)", cell)
    assert match is not None
    column = 0
    for char in match.group(1):
        column = column * 26 + ord(char) - ord("A") + 1
    row = int(match.group(2))
    return package.read_rows(sheet, row, row, column, column)[0][0]


def test_build_populates_only_inputs_and_preserves_formulas(
    service: ExcelEstimateService,
) -> None:
    output = service.build(_payload())
    after = _WorkbookPackage(output)

    assert output.startswith(b"PK")
    assert after.formula_fingerprint() == service._template_formula_fingerprint
    assert not any("#REF!" in formula for _, _, formula in after.formula_fingerprint())
    assert after.read_rows("Проверки", 13, 13, 5, 5)[0] == ["PASS"]
    assert any(
        sheet == "Проверки" and cell == "B6" and "MATCH" in formula
        for sheet, cell, formula in after.formula_fingerprint()
    )
    expected_general_values = {
        "project_name": "Миграция платформы",
        "project_type_code": "SUP_IT_Implementation",
        "estimate_date": (date(2026, 8, 20) - date(1899, 12, 30)).days,
        "vat_rate": 0.22,
        "discount_rate": 0.05,
        "work_hours_per_day": 8,
        "default_hours_basis": "Авто",
        "main_assumption": "Доступы предоставляются до старта",
    }
    for field, expected in expected_general_values.items():
        assert _cell_value(after, "Ввод", service._general_input_cells[field]) == expected
    assert "confidence" not in service._general_input_cells
    expected_archived_values = {
        "estimate_mode": "Уточнённая",
        "confidence": 0.85,
        "planned_start_date": (date(2026, 9, 1) - date(1899, 12, 30)).days,
        "source_or_spec_version": "ТЗ v3",
        "commercial_reserve_rate": 0.1,
    }
    for field, expected in expected_archived_values.items():
        assert _cell_value(
            after,
            "Технические данные",
            service._archived_input_cells[field],
        ) == expected

    work_rows = after.read_rows("Расчёт", 6, 8, 1, 22)
    assert work_rows[0][0:6] == [
        1,
        "Проектирование",
        "1.1",
        "Спроектировать целевую архитектуру",
        "L3 Архитектор | #480",
        24,
    ]
    assert work_rows[0][6:9] == [
        "24 ч определены по объёму проектирования.",
        "Всего",
        None,
    ]
    assert work_rows[1][4] == "L3 Windows инженер | #479"
    assert work_rows[2][0:6] == [
        2,
        "Внедрение",
        1,
        "Выполнить внедрение",
        "L3 DevOps-инженер | #1202",
        40,
    ]
    assert work_rows[2][6:9] == [None, "На единицу", 3]
    assert work_rows[0][21] == "Основной контур"
    assert after.read_rows("Итого по проекту", 15, 16, 6, 6) == [
        ["Нужен для согласования целевой архитектуры."],
        ["Нужен для внедрения согласованного решения."],
    ]
    assert after.read_rows("Внешние затраты", 6, 6, 1, 11)[0] == [
        "ПО",
        "Подписка на систему мониторинга",
        2,
        "лицензия",
        100000,
        None,
        "Разово",
        None,
        None,
        None,
        None,
    ]
    expected_assumption = {
        "type": "Риск",
        "text": "Срок выдачи доступов не подтверждён",
        "source": "ТЗ v3",
        "impact": "Возможен сдвиг старта",
    }
    for field, expected in expected_assumption.items():
        assert _cell_value(after, "Ввод", service._assumption_input_cells[field][0]) == expected


def test_dynamic_parameters_are_resolved_by_template_metadata(
    service: ExcelEstimateService,
) -> None:
    output = service.build(_payload())
    package = _WorkbookPackage(output)
    definitions = service.parameter_definitions("SUP_IT_Implementation")
    values = {
        definition.influence_code: _cell_value(
            package,
            "Ввод",
            service._parameter_input_cells["SUP_IT_Implementation"][definition.slot_number],
        )
        for definition in definitions
        if definition.influence_code != "INFO"
    }
    assert values["QTY"] == 12
    assert values["COMPLEXITY"] == 1.2
    assert values["RISK_PCT"] == 0.1


def test_current_role_rates_are_written_to_visible_role_directory(
    service: ExcelEstimateService,
) -> None:
    output = service.build(
        _payload(role_rate_overrides={"devops_l3": (4500, 2900)})
    )
    package = _WorkbookPackage(output)

    assert package.read_rows("Справочник ролей", 16, 16, 1, 6)[0] == [
        "1202",
        "DIT",
        "L3 DevOps-инженер | #1202",
        4500,
        2900,
        "Актуальная ставка проекта",
    ]


def test_customer_facing_formats_match_their_values(
    service: ExcelEstimateService,
) -> None:
    workbook = openpyxl.load_workbook(BytesIO(service.build(_payload())))
    summary = workbook["Итого по проекту"]
    calculation = workbook["Расчёт"]

    assert summary["C8"].number_format == r"dd\.mm\.yyyy"
    assert calculation["Q6"].number_format == "#,##0.0"
    assert calculation["T6"].number_format == "#,##0.0"
    assert calculation["M6"].number_format == '#,##0" ₽"'
    assert calculation["N6"].number_format == '#,##0" ₽"'
    assert calculation["R6"].number_format == '#,##0" ₽"'
    assert calculation["S6"].number_format == '#,##0" ₽"'
    assert calculation["O6"].number_format == "General"
    assert calculation.row_dimensions[6].height >= 50


def test_hours_rationale_is_written_for_a_manager_not_as_model_debug_data() -> None:
    rationale = _hours_rationale(
        {
            "name": "Определить общий результат и критерии успеха",
            "estimate_method": "parametric",
            "effort_min_hours": 13,
            "effort_max_hours": 19,
            "estimation_drivers": ["цели", "стейкхолдеры"],
            "context_facts": [
                "Предмет работ: Формирование первой и второй линий поддержки 1С ERP",
                "Канал предоставления услуг: Услуги предоставляются удалённо",
            ],
        },
        {
            "effort_hours": 14.5,
            "responsibility": "Выполнение основной части работы",
            "rationale": "Профиль technical; коэффициент объёма 1.00.",
        },
    )

    assert rationale == (
        "На выполнение работы «Определить общий результат и критерии успеха» "
        "заложено 14,5 ч. Ориентир для работ такого объёма — 13–19 ч; в смету "
        "включено наиболее вероятное значение 14,5 ч. Оценка учитывает "
        "согласование целей и критериев результата, взаимодействие с "
        "заинтересованными сторонами. Также учтено, что предмет работ — "
        "формирование первой и второй линий поддержки 1С ERP; канал "
        "предоставления услуг — услуги предоставляются удалённо."
    )
    assert "parametric" not in rationale
    assert "technical" not in rationale
    assert "коэффициент" not in rationale


def test_customer_explanation_replaces_internal_scope_term() -> None:
    rationale = _hours_rationale(
        {
            "name": "Уточнить scope проекта",
            "effort_hours": 8,
            "context_facts": ["Scope: комплексная поддержка"],
        }
    )

    assert "scope" not in rationale.casefold()
    assert "объём работ проекта" in rationale


def test_compacted_hours_explanation_keeps_the_disclosure_complete() -> None:
    rationale = (
        "На выполнение работы заложено 14,5 ч. "
        + "Оценка учитывает согласование результата с заказчиком. " * 12
    )

    result = _explain_compacted_hours(rationale, 16, 1.5)

    assert len(result) <= 600
    assert result.startswith("На выполнение работы заложено 16 ч.")
    assert result.endswith(
        "В эту сумму включено 1,5 ч участия смежного специалиста "
        "для проверки и согласования результата."
    )


def test_input_binding_follows_template_labels_instead_of_fixed_columns(
    service: ExcelEstimateService,
) -> None:
    assert service._general_input_cells["vat_rate"] == "B7"
    assert service._archived_input_cells["commercial_reserve_rate"] == "B8"
    assert "confidence" not in service._general_input_cells
    assert service._work_input_columns["comment"] == "G"
    assert service._work_input_columns["hours_basis"] == "H"
    assert service._work_input_columns["site_or_contour"] == "V"
    assert service.work_row_capacity == 50


def test_rejects_unknown_roles_and_invalid_percent_parameters(
    service: ExcelEstimateService,
) -> None:
    payload = _payload()
    payload.work_items[0].role_assignments[0].role = "Несуществующая роль"
    with pytest.raises(ExcelEstimateError, match="unknown role"):
        service.build(payload)

    payload = _payload(type_parameters=[{"influence_code": "RISK_PCT", "value": 10}])
    with pytest.raises(ExcelEstimateError, match="decimal fraction"):
        service.build(payload)


def test_rejects_more_than_template_expanded_role_rows(
    service: ExcelEstimateService,
) -> None:
    work = _payload().work_items[0].model_copy(deep=True)
    work.role_assignments = work.role_assignments * 26
    payload = _payload(work_items=[work])
    with pytest.raises(ExcelEstimateError, match="workbook limit is 50"):
        service.build(payload)


def test_rejects_monthly_cost_without_term_parameter(
    service: ExcelEstimateService,
) -> None:
    payload = _payload()
    payload.external_costs[0].periodicity = "В месяц"
    with pytest.raises(ExcelEstimateError, match="TERM_MONTHS"):
        service.build(payload)


def test_unused_required_target_margin_gets_neutral_value(
    service: ExcelEstimateService,
) -> None:
    payload = _payload(
        project_type_code="SUP_Cloud_PaaS",
        type_parameters=[],
        external_costs=[],
    )
    output = service.build(payload)
    package = _WorkbookPackage(output)
    margin = next(
        item
        for item in service.parameter_definitions("SUP_Cloud_PaaS")
        if item.influence_code == "TARGET_MARGIN"
    )
    assert (
        _cell_value(
            package,
            "Ввод",
            service._parameter_input_cells["SUP_Cloud_PaaS"][margin.slot_number],
        )
        == 0
    )


def test_required_target_margin_is_not_invented_for_external_costs(
    service: ExcelEstimateService,
) -> None:
    payload = _payload(
        project_type_code="SUP_Cloud_PaaS",
        type_parameters=[],
        external_costs=[
            {
                "category": "Облако",
                "description": "Облачная платформа",
                "quantity": 1,
                "unit": "месяц",
                "unit_cost": 100000,
                "periodicity": "В месяц",
            }
        ],
    )
    with pytest.raises(ExcelEstimateError, match="Целевая маржа"):
        service.build(payload)


def test_estimate_endpoints_return_metadata_and_xlsx(
    service: ExcelEstimateService,
) -> None:
    app = FastAPI()
    app.state.excel_estimate_service = service
    app.include_router(router)
    with TestClient(app) as client:
        parameters = client.get(
            "/api/v1/project-types/SUP_IT_Implementation/estimate-parameters"
        )
        assert parameters.status_code == 200
        assert len(parameters.json()) == 8

        response = client.post(
            "/api/v1/estimates/workbook",
            json=_payload().model_dump(mode="json"),
        )
        assert response.status_code == 200
        assert response.content.startswith(b"PK")
        assert response.headers["x-excel-recalculation"] == "required-on-open"
        assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_analysis_report_is_mapped_into_the_production_template(
    service: ExcelEstimateService,
) -> None:
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    project = Project(id=project_id, name="Проект из нейросети")
    run = AnalysisRun(
        id=run_id,
        project_id=project_id,
        status="ready",
        current_step="completed",
        input_document_ids=[str(uuid.uuid4())],
        errors=[],
    )
    result = ProjectAnalysis(
        run_id=run_id,
        project_id=project_id,
        project_type_code="SUP_IT_Implementation",
        confidence="high",
        summary="Проект внедрения",
        rationale="Тип определён по требованиям к миграции",
        facts=[],
        assumptions=["Доступы предоставляются до старта"],
        issues=[
            {
                "code": "access_delay",
                "description": "Возможна задержка доступов",
                "severity": "high",
                "impact_on_estimate": "Сдвиг календарного срока",
                "source_document_ids": ["doc-1"],
            }
        ],
        gaps=[],
        questions=[],
        warnings=[],
        document_digests=[],
        source_document_ids=[],
        raw_result={
            "stage_plan": {
                "stages": [
                    {
                        "code": "design",
                        "order": 1,
                        "name": "Проектирование",
                        "status": "selected",
                        "objective": "Согласовать целевую архитектуру",
                        "selection_reason": "Этап обязателен для внедрения",
                        "deliverables": ["Утверждённая архитектура"],
                    }
                ]
            },
            "work_plan": {
                "packages": [
                    {
                        "stage_code": "design",
                        "works": [
                            {
                                "work_code": "design.target",
                                "name": "Спроектировать целевую архитектуру",
                                "selection_reason": "Работа выбрана нейросетью",
                                "assumptions": [],
                                "role_assignments": [
                                    {
                                        "role_code": "technical_architect",
                                        "effort_hours": 24,
                                        "responsibility": "Архитектура",
                                        "rationale": "Нужны три рабочих дня архитектора.",
                                    },
                                    {
                                        "role_code": "dba_l3",
                                        "effort_hours": 8,
                                        "responsibility": "Проектирование БД",
                                        "rationale": "Нужен один рабочий день DBA.",
                                    },
                                ],
                            }
                        ],
                    }
                ]
            },
        },
        model_name="test-model",
        prompt_version="test-prompt",
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    payload = _analysis_estimate_payload(project, result, run)
    output = service.build(payload)
    package = _WorkbookPackage(output)

    assert payload.project_name == "Проект из нейросети"
    assert payload.confidence == 0.9
    assert package.read_rows("Ввод", 4, 5, 2, 2) == [
        ["Проект из нейросети"],
        ["SUP_IT_Implementation"],
    ]
    rows = package.read_rows("Расчёт", 6, 7, 1, 8)
    assert rows[0][0:6] == [
        1,
        "Проектирование",
        "1.1",
        "Спроектировать целевую архитектуру",
        "L3 Архитектор | #480",
        24,
    ]
    assert rows[0][6] == (
        "На выполнение работы «Спроектировать целевую архитектуру» заложено "
        "24 ч. В это время входит архитектура."
    )
    assert rows[0][7] == "Всего"
    assert rows[1][4:6] == ["L3 DBA инженер | #1207", 8]
    assert package.read_rows("Итого по проекту", 15, 15, 6, 6)[0][0] == (
        "Этап включён, чтобы согласовать целевую архитектуру. Он необходим, "
        "потому что этап обязателен для внедрения. По итогам заказчик получает: "
        "Утверждённая архитектура."
    )
    assert package.read_rows("Ввод", 36, 36, 1, 4)[0] == [
        "Риск",
        "Возможна задержка доступов",
        "doc-1",
        "Сдвиг календарного срока",
    ]


def test_extracted_facts_are_mapped_only_to_matching_project_parameters(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [
            {"name": "Срок обслуживания", "value": "6 месяцев"},
            {"name": "Пользователей", "value": "1200 человек"},
            {"name": "Обращений в месяц", "value": "350 + 10% = 385 шт."},
            {"name": "Название системы", "value": "EasyDesk"},
        ],
    )
    by_slot = {item["slot_number"]: item["value"] for item in mapped}
    assert by_slot[1] == 1200
    assert by_slot[2] == 6
    assert by_slot[4] == 385


def test_explicit_type_parameter_wins_over_inferred_fact(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [{"name": "Срок обслуживания", "value": "6 месяцев"}],
        [{"influence_code": "TERM_MONTHS", "value": 9}],
    )
    assert mapped == [{"influence_code": "TERM_MONTHS", "value": 9}]


def test_support_volume_is_not_confused_with_contract_term(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [
            {"name": "Объем 1-й линии", "value": "Не более 350 заявок в месяц"},
            {"name": "Объем 2-й линии", "value": "Не более 70 заявок в месяц"},
        ],
    )
    assert mapped == [{"slot_number": 4, "value": 350}]


def test_sla_minutes_and_calendar_year_are_not_contract_months(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [
            {
                "name": "SLA регистрации",
                "value": "15 минут для запросов на обслуживание; 90% в срок",
            },
            {
                "name": "Срок завершения",
                "value": "Не позднее сентября 2024 года",
            },
        ],
    )
    assert all(item["slot_number"] != 2 for item in mapped)


def test_year_duration_is_converted_to_months(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [{"name": "Срок договора", "value": "2 года"}],
    )
    assert {item["slot_number"]: item["value"] for item in mapped}[2] == 24


def test_role_level_number_does_not_occupy_volume_parameter(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [
            {
                "name": "Основной предмет работ",
                "value": "Формирование 1-ю линии и 2-ю линию поддержки 1С ERP",
            },
            {"name": "Объем 1-й линии", "value": "350 заявок в месяц"},
        ],
    )
    assert mapped == [{"slot_number": 4, "value": 350}]


def test_strong_parameter_fact_wins_regardless_of_fact_order(
    service: ExcelEstimateService,
) -> None:
    mapped = service.infer_type_parameters(
        "SUP_L1",
        [
            {"name": "Описание", "value": "1 инцидент в примере"},
            {"name": "Объем обращений", "value": "350 заявок в месяц"},
        ],
    )
    assert mapped == [{"slot_number": 4, "value": 350}]
