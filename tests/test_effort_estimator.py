from pathlib import Path

import pytest

from app.effort_estimator import (
    AIAssignment,
    AIDirectEstimationResult,
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


def test_direct_ai_plan_selects_its_own_scope_roles_and_hours() -> None:
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
                    assignments=[
                        AIAssignment(
                            role_code="support_l1",
                            effort_hours=7.25,
                            responsibility="Приём и первичная обработка обращений",
                        ),
                        AIAssignment(
                            role_code="support_supervisor",
                            effort_hours=1.25,
                            responsibility="Контроль качества обслуживания",
                        ),
                    ],
                )
            ],
            scope_risks=["Не указан фактический поток обращений."],
        ),
    )

    assert result.estimation_mode == "not_estimated"
    assert [work.work_code for package in result.packages for work in package.works] == [
        selected.work_code
    ]
    work = _work(result, selected.work_code)
    assert work.estimate_method == "expert"
    assert work.effort_hours == 8.5
    assert {item.role_code for item in work.role_assignments} == {
        "support_l1",
        "support_supervisor",
    }


def test_direct_ai_plan_rejects_unknown_catalogue_work_or_role() -> None:
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
                        assignments=[
                            AIAssignment(
                                role_code="support_l1",
                                effort_hours=1,
                                responsibility="Тест",
                            )
                        ],
                    )
                ]
            ),
        )
