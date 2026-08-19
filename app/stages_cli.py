from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.stage_contracts import StagePlanContext
from app.stage_planner import StagePlanner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="projectile-stages",
        description="Validate the MONS stage catalog or build a project stage plan.",
    )
    parser.add_argument(
        "--project-types",
        type=Path,
        default=Path("data/project-types.json"),
    )
    parser.add_argument(
        "--stage-catalog",
        type=Path,
        default=Path("data/project-stage-templates.json"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate catalog structure and coverage.")
    subparsers.add_parser("list", help="List supported project-type codes.")
    plan = subparsers.add_parser("plan", help="Build a resolved stage plan.")
    plan.add_argument("project_type_code")
    plan.add_argument("--signal", action="append", default=[])
    plan.add_argument("--include-stage", action="append", default=[])
    plan.add_argument("--exclude-stage", action="append", default=[])
    plan.add_argument(
        "--only-selected",
        action="store_true",
        help="Omit unresolved candidate stages from the output.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    planner = StagePlanner.from_files(args.project_types, args.stage_catalog)
    if args.command == "validate":
        print(
            json.dumps(
                {
                    "status": "ok",
                    "schema_version": planner.catalog.schema_version,
                    "project_type_profiles": len(planner.profile_codes()),
                    "templates": len(planner.catalog.templates),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "list":
        print(json.dumps(planner.profile_codes(), ensure_ascii=False, indent=2))
        return
    plan = planner.build_plan(
        args.project_type_code,
        StagePlanContext(
            signals=args.signal,
            include_stage_codes=args.include_stage,
            exclude_stage_codes=args.exclude_stage,
            include_candidates=not args.only_selected,
        ),
    )
    print(plan.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
