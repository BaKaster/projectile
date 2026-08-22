from pathlib import Path

import pytest

from app.effort_estimator import (
    AIDirectEstimationResult,
    AIScopeReviewResult,
    AIWorkEstimate,
    AdaptiveEffortEstimator,
)
from app.stage_contracts import StagePlanContext
from app.stage_planner import StagePlanner
from app.work_contracts import WorkFact, WorkPlanContext
from app.work_generator import WorkGenerator


def _dependencies():
    planner = StagePlanner.from_files(
        Path("data/project-types.json"), Path("data/project-stage-templates.json")
    )
    generator = WorkGenerator.from_file(Path("data/project-work-templates.json"), planner)
    estimator = AdaptiveEffortEstimator.from_file(Path("data/role-effort-catalog.json"))
    return planner, generator, estimator


def _work(plan, code):
    return next(
        work
        for package in plan.packages
        for work in package.works
        if work.work_code == code
    )


def test_estimator_assigns_roles_rates_and_consistent_totals() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation",
        StagePlanContext(signals=["migration"], include_candidates=False),
    )

    plan = estimator.estimate(generator.generate(stage_plan, WorkPlanContext(signals=["migration"])))

    assert plan.estimation_mode == "deterministic"
    assert plan.total_effort_hours == sum(
        assignment.effort_hours
        for package in plan.packages
        for work in package.works
        for assignment in work.role_assignments
    )
    assert plan.total_sale_amount_rub > plan.total_cost_amount_rub > 0
    for package in plan.packages:
        for work in package.works:
            assert work.role_code != "unassigned"
            assert work.effort_min_hours <= work.effort_hours <= work.effort_max_hours
            assert work.effort_hours == sum(item.effort_hours for item in work.role_assignments)


def test_numeric_scope_fact_increases_only_relevant_work_estimate() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation",
        StagePlanContext(signals=["migration"], include_candidates=False),
    )
    baseline = estimator.estimate(
        generator.generate(stage_plan, WorkPlanContext(signals=["migration"]))
    )
    scoped = estimator.estimate(
        generator.generate(
            stage_plan,
            WorkPlanContext(
                signals=["migration"],
                facts=[
                    WorkFact(
                        name="Количество переносимых конфигураций",
                        value="64 конфигурации",
                        source_document_ids=["brief"],
                    )
                ],
            ),
        )
    )

    code = "cutover_migration.migrate_configuration_data"
    assert _work(scoped, code).effort_hours > _work(baseline, code).effort_hours
    assert _work(scoped, code).estimate_method == "parametric"


def test_scope_boundaries_scale_asset_work_but_not_unrelated_reporting() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_IT_Audit",
        StagePlanContext(signals=["multi_site"], include_candidates=False),
    )
    baseline = estimator.estimate(
        generator.generate(stage_plan, WorkPlanContext(signals=["onsite_work", "multi_site"]))
    )
    scoped = estimator.estimate(
        generator.generate(
            stage_plan,
            WorkPlanContext(
                signals=["onsite_work", "multi_site"],
                facts=[
                    WorkFact(
                        name="Границы работ",
                        value="100 серверов, 100 единиц сетевого оборудования, 2 площадки",
                        source_document_ids=["brief"],
                    )
                ]
            ),
        )
    )

    assert _work(
        scoped, "evidence_collection.inventory_assets"
    ).effort_hours > _work(
        baseline, "evidence_collection.inventory_assets"
    ).effort_hours
    assert _work(scoped, "audit_report.prepare_report").effort_hours == _work(
        baseline, "audit_report.prepare_report"
    ).effort_hours


def test_telemetry_metric_count_does_not_scale_monthly_monitoring_capacity() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_App_Support", StagePlanContext(include_candidates=False)
    )
    baseline = estimator.estimate(generator.generate(stage_plan, WorkPlanContext()))
    with_metrics = estimator.estimate(
        generator.generate(
            stage_plan,
            WorkPlanContext(
                facts=[
                    WorkFact(
                        name="Объём метрик мониторинга",
                        value="24 метрики",
                        source_document_ids=["brief"],
                    )
                ]
            ),
        )
    )

    code = "service_operation.monitor_and_restore"
    assert _work(with_metrics, code).effort_hours == _work(baseline, code).effort_hours


def test_sla_duration_does_not_scale_incident_capacity() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_App_Support", StagePlanContext(include_candidates=False)
    )
    baseline = estimator.estimate(generator.generate(stage_plan, WorkPlanContext()))
    with_sla = estimator.estimate(
        generator.generate(
            stage_plan,
            WorkPlanContext(
                facts=[
                    WorkFact(
                        name="SLA по критичным обращениям",
                        value="Реакция 15 минут, решение 4 часа",
                        source_document_ids=["brief"],
                    )
                ]
            ),
        )
    )

    code = "service_operation.handle_incidents_requests"
    assert _work(with_sla, code).effort_hours == _work(baseline, code).effort_hours


def test_managed_component_count_scales_recurring_operations() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_App_Support", StagePlanContext(include_candidates=False)
    )
    baseline = estimator.estimate(generator.generate(stage_plan, WorkPlanContext()))
    scoped = estimator.estimate(
        generator.generate(
            stage_plan,
            WorkPlanContext(
                facts=[
                    WorkFact(
                        name="Состав управляемых компонентов",
                        value="5 компонентов",
                        source_document_ids=["brief"],
                    )
                ]
            ),
        )
    )

    for code in (
        "service_operation.perform_routine_operations",
        "service_operation.monitor_and_restore",
    ):
        assert _work(scoped, code).effort_hours > _work(baseline, code).effort_hours


def test_security_work_uses_security_catalog_role() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SEC_Audit",
        StagePlanContext(include_candidates=False),
    )
    plan = estimator.estimate(
        generator.generate(stage_plan, WorkPlanContext(signals=["regulated_scope"]))
    )

    work = _work(plan, "current_state_assessment.scan_vulnerabilities")
    assert work.role_code == "pentester"
    assert work.role_assignments[0].sale_rate_rub_per_hour == 5500
    assert work.role_assignments[0].cost_rate_rub_per_hour == 5200


def test_support_projects_cannot_receive_security_roles() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_L1", StagePlanContext(include_candidates=False)
    )
    plan = estimator.estimate(generator.generate(stage_plan))
    assigned = {
        assignment.role_code
        for package in plan.packages
        for work in package.works
        for assignment in work.role_assignments
    }
    assert "pentester" not in assigned
    assert not any(code.startswith("security_") for code in assigned)


def test_direct_ai_plan_selects_scope_but_keeps_catalogue_roles_and_hours() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())
    selected = next(work for package in candidate.packages for work in package.works)

    result = estimator._apply_direct_ai_plan(
        candidate,
        AIDirectEstimationResult(
            works=[
                AIWorkEstimate(
                    work_code=selected.work_code,
                    rationale="Работа прямо следует из подтверждённого объёма поддержки.",
                    evidence=["ТЗ прямо требует приём и обработку обращений."],
                    hours_basis="В месяц",
                )
            ],
            scope_risks=["Не указан фактический поток обращений."],
        ),
    )

    expected = _work(estimator.estimate(candidate), selected.work_code)
    assert result.estimation_mode == "deterministic"
    assert [work.work_code for package in result.packages for work in package.works] == [
        selected.work_code
    ]
    work = _work(result, selected.work_code)
    assert work.estimate_method == expected.estimate_method
    assert work.effort_hours == expected.effort_hours
    assert [item.role_code for item in work.role_assignments] == [
        item.role_code for item in expected.role_assignments
    ]


def test_direct_ai_plan_rejects_unknown_catalogue_work() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())

    with pytest.raises(ValueError, match="unknown work"):
        estimator._apply_direct_ai_plan(
            candidate,
            AIDirectEstimationResult(
                works=[
                    AIWorkEstimate(
                        work_code="invented.work",
                        rationale="Не должно быть принято.",
                        evidence=["Тестовая строка."],
                        hours_basis="Всего",
                    )
                ]
            ),
        )


def test_scope_review_removes_work_not_supported_by_evidence() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())
    works = [work for package in candidate.packages for work in package.works]
    assert len(works) >= 2
    proposal = AIDirectEstimationResult(
        works=[
            AIWorkEstimate(
                work_code=work.work_code,
                rationale="Черновой вариант.",
                evidence=["Факт из ТЗ."],
                hours_basis="В месяц",
            )
            for work in works[:2]
        ]
    )
    result = estimator._apply_direct_ai_plan(
        candidate,
        proposal,
        AIScopeReviewResult(
            lifecycle_state="existing_solution",
            delivery_intent="support",
            approved_work_codes=[works[0].work_code],
            rejected_scope_risks=["Вторая работа не подтверждена ТЗ."],
            review_rationale="Оставлена только подтверждённая работа поддержки.",
        ),
    )

    assert [work.work_code for package in result.packages for work in package.works] == [
        works[0].work_code
    ]
