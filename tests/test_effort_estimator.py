from pathlib import Path

from app.effort_estimator import AdaptiveEffortEstimator
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
