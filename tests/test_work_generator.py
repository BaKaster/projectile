from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.stage_contracts import StagePlanContext
from app.stage_planner import StagePlanner
from app.work_contracts import ProjectSpecificWork, WorkFact, WorkPlanContext
from app.work_generator import WorkGenerationError, WorkGenerator


@pytest.fixture(scope="module")
def planner() -> StagePlanner:
    return StagePlanner.from_files(
        Path("data/project-types.json"),
        Path("data/project-stage-templates.json"),
    )


@pytest.fixture(scope="module")
def generator(planner: StagePlanner) -> WorkGenerator:
    return WorkGenerator.from_file(Path("data/project-work-templates.json"), planner)


def _work_codes(plan) -> set[str]:
    return {work.work_code for package in plan.packages for work in package.works}


def test_catalog_is_self_contained_and_covers_every_project_type(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    raw_catalog = Path("data/project-work-templates.json").read_text(encoding="utf-8")
    assert "Данные для стажёров" not in raw_catalog
    assert "company_source_refs" not in raw_catalog
    assert set(generator.specialization_codes()) == set(planner.profile_codes())

    for project_type_code in planner.profile_codes():
        stage_plan = planner.build_plan(
            project_type_code, StagePlanContext(include_candidates=False)
        )
        work_plan = generator.generate(stage_plan)
        work_plan.validate_against(stage_plan)


def test_conditional_work_depends_on_current_project_signals(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation",
        StagePlanContext(
            signals=["migration", "data_transfer"], include_candidates=False
        ),
    )
    baseline = generator.generate(
        stage_plan,
        WorkPlanContext(signals=["migration", "data_transfer"]),
    )
    baseline_codes = _work_codes(baseline)
    assert "cutover_migration.migrate_configuration_data" in baseline_codes
    assert "solution_design.design_integrations" not in baseline_codes

    with_integration = generator.generate(
        stage_plan,
        WorkPlanContext(signals=["migration", "data_transfer", "integration"]),
    )
    assert "solution_design.design_integrations" in _work_codes(with_integration)


def test_project_facts_are_attached_to_relevant_work_and_sources(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation",
        StagePlanContext(signals=["migration"], include_candidates=False),
    )
    work_plan = generator.generate(
        stage_plan,
        WorkPlanContext(
            signals=["migration", "data_migration"],
            facts=[
                WorkFact(
                    name="Объём переносимых данных",
                    value="2 ТБ в трёх волнах",
                    source_document_ids=["tz-document"],
                )
            ],
        ),
    )
    migration = next(
        work
        for package in work_plan.packages
        for work in package.works
        if work.work_code == "cutover_migration.migrate_configuration_data"
    )
    assert migration.context_facts == ["Объём переносимых данных: 2 ТБ в трёх волнах"]
    assert migration.source_document_ids == ["tz-document"]
    assert migration.effort_hours is None
    assert migration.estimate_method is None


def test_explicit_work_override_is_validated(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_HW", StagePlanContext(include_candidates=False)
    )
    plan = generator.generate(
        stage_plan,
        WorkPlanContext(exclude_work_codes=["supply_sourcing.request_quotes"]),
    )
    assert "supply_sourcing.request_quotes" not in _work_codes(plan)

    with pytest.raises(WorkGenerationError, match="unknown work codes"):
        generator.generate(
            stage_plan,
            WorkPlanContext(include_work_codes=["supply_contract.invented"]),
        )


def test_project_specific_work_is_added_from_current_project_context(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation", StagePlanContext(include_candidates=False)
    )
    work_plan = generator.generate(
        stage_plan,
        WorkPlanContext(
            project_specific_works=[
                ProjectSpecificWork(
                    stage_code="solution_design",
                    name="Разработать адаптер для проприетарной шины заказчика",
                    rationale="Интерфейс шины явно указан в ТЗ и отсутствует в типовом scope",
                    outputs=["Спецификация и реализованный адаптер"],
                    estimation_drivers=["Количество типов сообщений"],
                    source_document_ids=["tz-document"],
                )
            ]
        ),
    )
    custom = next(
        work
        for package in work_plan.packages
        for work in package.works
        if ".custom." in work.work_code
    )
    assert custom.name == "Разработать адаптер для проприетарной шины заказчика"
    assert custom.outputs == ["Спецификация и реализованный адаптер"]
    assert custom.source_document_ids == ["tz-document"]
    assert custom.selection_reason.startswith("Проектно-специфичная работа:")


def test_project_specific_work_cannot_target_an_unselected_stage(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation", StagePlanContext(include_candidates=False)
    )
    with pytest.raises(
        WorkGenerationError, match="project-specific works may only target selected stages"
    ):
        generator.generate(
            stage_plan,
            WorkPlanContext(
                project_specific_works=[
                    ProjectSpecificWork(
                        stage_code="pilot",
                        name="Проверить уникальный сценарий",
                        rationale="Требование ТЗ",
                        outputs=["Протокол проверки"],
                        source_document_ids=["tz-document"],
                    )
                ]
            ),
        )


def test_work_plan_api_returns_adaptive_contract(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    app = FastAPI()
    app.state.stage_planner = planner
    app.state.work_generator = generator
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/project-types/SUP_IT_Implementation/work-plan",
            json={
                "stage_context": {
                    "signals": ["migration"],
                    "include_candidates": False,
                },
                "work_context": {
                    "signals": ["migration", "integration"],
                    "facts": [
                        {
                            "name": "Количество интеграций",
                            "value": "4 внешние системы",
                            "source_document_ids": ["brief"],
                        }
                    ],
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["work_catalog_version"] == "1.0.0"
    assert any(
        work["work_code"] == "solution_design.design_integrations"
        for package in payload["packages"]
        for work in package["works"]
    )


def test_support_lifecycle_separates_one_time_and_monthly_work(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_L1",
        StagePlanContext(signals=["incumbent_transition"], include_candidates=False),
    )
    plan = generator.generate(stage_plan)
    by_stage = {
        package.stage_code: {work.hours_basis for work in package.works}
        for package in plan.packages
    }
    assert by_stage["service_transition"] == {"Всего"}
    assert by_stage["service_operation"] == {"В месяц"}
    assert by_stage["service_improvement"] == {"В месяц"}
    assert by_stage["service_governance"] == {"В месяц"}


def test_application_support_exposes_point_change_for_integration_signal(
    planner: StagePlanner, generator: WorkGenerator
) -> None:
    stage_plan = planner.build_plan(
        "SUP_App_Support",
        StagePlanContext(include_candidates=False),
    )
    plan = generator.generate(
        stage_plan,
        WorkPlanContext(signals=["integration"], scope_mode="baseline"),
    )
    change = next(
        work
        for package in plan.packages
        for work in package.works
        if work.work_code == "service_operation.implement_point_change"
    )
    assert change.hours_basis == "Всего"
    assert not any(
        work.work_code == "service_operation.fulfill_changes"
        for package in plan.packages
        for work in package.works
    )


def test_quantified_capacity_fact_is_retained_in_limited_work_context(
    generator: WorkGenerator,
) -> None:
    facts = [
        WorkFact(
            name=f"Описание поддерживаемого сервиса {index}",
            value="Подробное описание эксплуатации сервиса без количественного объёма.",
            source_document_ids=["brief"],
        )
        for index in range(4)
    ]
    capacity = WorkFact(
        name="Объём поддерживаемых сервисов",
        value="2 сервиса",
        source_document_ids=["brief"],
    )
    relevant = generator._relevant_facts(
        [*facts, capacity], ["поддерживаемые сервисы"]
    )

    assert capacity in relevant
