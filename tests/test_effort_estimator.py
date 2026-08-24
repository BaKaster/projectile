from pathlib import Path

import pytest

from app.effort_estimator import (
    AIAssignment,
    AIDirectEstimationResult,
    AIScopeReviewResult,
    AIWorkEstimate,
    AdaptiveEffortEstimator,
    infer_contract_term,
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


def _ai_work(work, *, role_code: str = "technical_team_lead", hours: float = 40) -> AIWorkEstimate:
    return AIWorkEstimate(
        work_code=work.work_code,
        rationale="Работа и объём следуют из подтверждённых фактов проекта.",
        evidence=["ТЗ прямо подтверждает результат и объём 20 объектов."],
        hours_basis=work.hours_basis,
        assignments=[
            AIAssignment(
                role_code=role_code,
                effort_hours=hours,
                responsibility="Выполнение подтверждённого объёма",
            )
        ],
        effort_hours=hours,
        effort_min_hours=hours * 0.8,
        effort_max_hours=hours * 1.25,
    )


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


def test_direct_ai_plan_uses_ai_roles_and_hours_with_catalogue_rates() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())
    selected = [package.works[0] for package in candidate.packages]

    result = estimator._apply_direct_ai_plan(
        candidate,
        AIDirectEstimationResult(
            works=[_ai_work(work, role_code="support_l1", hours=120) for work in selected],
            contract_months=12,
            contract_months_evidence=["Срок оказания услуг — 12 месяцев."],
            scope_risks=["Не указан фактический поток обращений."],
        ),
    )

    assert result.estimation_mode == "ai_direct"
    assert result.contract_months == 12
    assert result.contract_months_evidence == ["Срок оказания услуг — 12 месяцев."]
    for selected_work in selected:
        work = _work(result, selected_work.work_code)
        assert work.estimate_method == "expert"
        assert work.effort_hours == 120
        assert [item.role_code for item in work.role_assignments] == ["support_l1"]
        assert work.role_assignments[0].sale_rate_rub_per_hour == 2100
    estimator._set_totals(result)
    assert result.one_time_effort_hours is not None
    assert result.monthly_effort_hours is not None
    assert result.contract_total_effort_hours == (
        result.one_time_effort_hours + result.monthly_effort_hours * 12
    )
    assert result.contract_total_sale_amount_rub == (
        result.one_time_sale_amount_rub + result.monthly_sale_amount_rub * 12
    )


def test_direct_ai_plan_rejects_unknown_catalogue_work() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())

    with pytest.raises(ValueError, match="unknown work"):
        estimator._apply_direct_ai_plan(
            candidate,
            AIDirectEstimationResult(
                works=[
                    _ai_work(candidate.packages[0].works[0]).model_copy(
                        update={"work_code": "invented.work"}
                    )
                ]
            ),
        )


def test_scope_review_removes_work_not_supported_by_evidence() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())
    required = [package.works[0] for package in candidate.packages]
    extra = next(
        work
        for package in candidate.packages
        for work in package.works[1:]
    )
    proposal = AIDirectEstimationResult(
        works=[*[_ai_work(work) for work in required], _ai_work(extra)]
    )
    result = estimator._apply_direct_ai_plan(
        candidate,
        proposal,
        AIScopeReviewResult(
            lifecycle_state="existing_solution",
            delivery_intent="support",
            approved_work_codes=[work.work_code for work in required],
            rejected_scope_risks=["Дополнительная работа не подтверждена ТЗ."],
            review_rationale="На каждом этапе оставлена необходимая подтверждённая работа.",
        ),
    )

    assert [work.work_code for package in result.packages for work in package.works] == [
        work.work_code for work in required
    ]


def test_direct_ai_plan_omits_catalogue_stages_without_approved_work() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())

    result = estimator._apply_direct_ai_plan(
        candidate,
        AIDirectEstimationResult(works=[_ai_work(candidate.packages[0].works[0])]),
    )

    assert [package.stage_code for package in result.packages] == [
        candidate.packages[0].stage_code
    ]
    assert all(package.works for package in result.packages)


def test_contract_duration_requires_document_evidence() -> None:
    planner, generator, _ = _dependencies()
    stage_plan = planner.build_plan("SUP_L1", StagePlanContext(include_candidates=False))
    candidate = generator.generate(stage_plan, WorkPlanContext())

    with pytest.raises(ValueError, match="duration requires document evidence"):
        AIDirectEstimationResult(
            works=[_ai_work(candidate.packages[0].works[0])],
            contract_months=12,
        )


def test_contract_term_is_extracted_from_explicit_service_duration() -> None:
    months, evidence = infer_contract_term(
        "Поддержка оказывается на абонентской основе сроком не менее одного года.",
        [],
    )

    assert months == 12
    assert evidence


def test_warranty_duration_is_not_used_as_service_contract_term() -> None:
    months, evidence = infer_contract_term(
        "Гарантийный срок оборудования составляет 3 года.",
        [],
    )

    assert months is None
    assert evidence == []


def test_ai_cannot_scale_hours_from_an_unquantified_list_number() -> None:
    planner, generator, estimator = _dependencies()
    stage_plan = planner.build_plan(
        "SUP_IT_Implementation", StagePlanContext(include_candidates=False)
    )
    candidate = estimator.estimate(generator.generate(stage_plan, WorkPlanContext()))
    work = candidate.packages[0].works[0]

    with pytest.raises(ValueError, match="without a confirmed quantity"):
        estimator._apply_direct_ai_plan(
            candidate,
            AIDirectEstimationResult(
                works=[
                    _ai_work(work, hours=10_000).model_copy(
                        update={"evidence": ["В перечне присутствуют позиции до №96."]}
                    )
                ]
            ),
        )
