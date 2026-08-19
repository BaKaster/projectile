from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.stage_contracts import StagePlanContext
from app.stage_planner import StagePlanner, StagePlanningError
from app.work_contracts import GeneratedWorkPlan, StageWorkPackage, WorkItem


@pytest.fixture(scope="module")
def planner() -> StagePlanner:
    return StagePlanner.from_files(
        Path("data/project-types.json"),
        Path("data/project-stage-templates.json"),
    )


def test_every_project_type_has_a_resolvable_profile(planner: StagePlanner) -> None:
    assert len(planner.profile_codes()) == 26
    for code in planner.profile_codes():
        plan = planner.build_plan(code)
        assert plan.project_type_code == code
        assert plan.stages
        assert any(stage.status == "selected" for stage in plan.stages)


def test_migration_signal_activates_cutover_stage(planner: StagePlanner) -> None:
    baseline = planner.build_plan("SUP_IT_Implementation")
    baseline_cutover = next(
        stage for stage in baseline.stages if stage.code == "cutover_migration"
    )
    assert baseline_cutover.status == "candidate"

    migration = planner.build_plan(
        "SUP_IT_Implementation",
        StagePlanContext(signals=["migration", "data_transfer", "pilot"]),
    )
    selected = {
        stage.code for stage in migration.stages if stage.status == "selected"
    }
    assert {"pilot", "cutover_migration"} <= selected


def test_support_profile_selects_transition_and_recurring_operation(
    planner: StagePlanner,
) -> None:
    plan = planner.build_plan("SUP_L2")
    selected = {stage.code for stage in plan.stages if stage.status == "selected"}
    assert {
        "partner_sourcing",
        "service_transition",
        "service_operation",
        "service_improvement",
    } <= selected
    operation = next(stage for stage in plan.stages if stage.code == "service_operation")
    assert operation.execution_mode == "recurring"


def test_only_selected_omits_unresolved_candidates(planner: StagePlanner) -> None:
    plan = planner.build_plan(
        "SUP_HW", StagePlanContext(include_candidates=False)
    )
    assert all(stage.status == "selected" for stage in plan.stages)
    assert "supply_installation" not in {stage.code for stage in plan.stages}


def test_unknown_signal_and_required_exclusion_are_rejected(
    planner: StagePlanner,
) -> None:
    with pytest.raises(StagePlanningError, match="unknown project signals"):
        planner.build_plan("SUP_HW", StagePlanContext(signals=["invented"]))
    with pytest.raises(StagePlanningError, match="required stage cannot be excluded"):
        planner.build_plan(
            "SUP_HW",
            StagePlanContext(exclude_stage_codes=["supply_requirements"]),
        )


def test_work_generator_contract_accepts_only_selected_stages(
    planner: StagePlanner,
) -> None:
    stage_plan = planner.build_plan(
        "SUP_HW", StagePlanContext(include_candidates=False)
    )
    packages = [
        StageWorkPackage(
            stage_code=stage.code,
            works=[
                WorkItem(
                    work_code=f"{stage.code}.work",
                    name=f"Работа этапа {stage.name}",
                    description="Тестовая работа по стабильному контракту.",
                    role_code="engineer",
                    estimate_method="norm",
                    effort_hours=1,
                )
            ],
        )
        for stage in stage_plan.stages
    ]
    work_plan = GeneratedWorkPlan(
        project_type_code=stage_plan.project_type_code,
        stage_schema_version=stage_plan.schema_version,
        packages=packages,
    )
    work_plan.validate_against(stage_plan)

    work_plan.packages[0].stage_code = "supply_installation"
    with pytest.raises(ValueError, match="selected stages"):
        work_plan.validate_against(stage_plan)


def test_stage_plan_api_returns_resolved_contract(planner: StagePlanner) -> None:
    app = FastAPI()
    app.state.stage_planner = planner
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/project-types/SUP_IT_Implementation/stage-plan",
            json={"signals": ["migration", "pilot"], "include_candidates": False},
        )
        unknown = client.post(
            "/api/v1/project-types/UNKNOWN/stage-plan", json={}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_type_code"] == "SUP_IT_Implementation"
    assert {"pilot", "cutover_migration"} <= {
        stage["code"] for stage in payload["stages"]
    }
    assert unknown.status_code == 404
