from pathlib import Path
from types import SimpleNamespace

from app.api import _recalculate_existing_analysis
from app.effort_estimator import AdaptiveEffortEstimator
from app.stage_planner import StagePlanner
from app.work_generator import WorkGenerator


ROOT = Path(__file__).resolve().parents[1]


def test_final_calculation_keeps_work_only_signals_out_of_stage_planner() -> None:
    stage_planner = StagePlanner.from_files(
        ROOT / "data" / "project-types.json",
        ROOT / "data" / "project-stage-templates.json",
    )
    work_generator = WorkGenerator.from_file(
        ROOT / "data" / "project-work-templates.json", stage_planner
    )
    effort_estimator = AdaptiveEffortEstimator.from_file(
        ROOT / "data" / "role-effort-catalog.json"
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                stage_planner=stage_planner,
                work_generator=work_generator,
                effort_estimator=effort_estimator,
            )
        )
    )
    analysis = SimpleNamespace(
        raw_result={
            "stage_signals": [
                {"code": "integration"},
                {"code": "recurring_changes"},
                {"code": "vendor_support"},
            ]
        },
        facts=[],
    )

    _recalculate_existing_analysis(request, analysis, "SUP_Complex")

    assert analysis.raw_result["stage_plan"]["project_type_code"] == "SUP_Complex"
    assert analysis.raw_result["work_plan"]["total_effort_hours"] > 0
