from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openpyxl import load_workbook


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "reference-project-test-cases.json"
TERMINAL_STATUSES = {"ready", "failed"}


@dataclass(frozen=True)
class Metric:
    value: float
    description: str


def _read_cell(path: Path, sheet: str, cell: str) -> float:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        value = workbook[sheet][cell].value
    finally:
        workbook.close()
    if not isinstance(value, (int, float)):
        raise ValueError(f"{path.name}: {sheet}!{cell} is not numeric: {value!r}")
    return float(value)


def _reference_metric(case: dict[str, Any], path: Path) -> Metric | None:
    definition = case.get("comparison_metric")
    if not definition:
        return None
    total = 0.0
    terms: list[str] = []
    for component in definition["components"]:
        value = _read_cell(path, component["sheet"], component["cell"])
        multiplier = float(component.get("multiplier", 1))
        total += value * multiplier
        terms.append(
            f"{component['sheet']}!{component['cell']}"
            + (f" x {multiplier:g}" if multiplier != 1 else "")
        )
    if definition.get("includes_vat"):
        total /= 1 + float(definition.get("vat_rate", 0.2))
    return Metric(total, " + ".join(terms))


def _generated_metric(path: Path) -> Metric:
    return Metric(_read_cell(path, "Итого по проекту", "I6"), "Итого по проекту!I6")


def _upload(
    client: httpx.Client,
    project_id: str,
    case_id: str,
    specification: Path,
    relative_path: str,
) -> None:
    media_type = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
    }.get(specification.suffix.casefold(), "application/octet-stream")
    transport_name = f"{case_id}{specification.suffix.lower()}"
    with specification.open("rb") as stream:
        response = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files={"files": (transport_name, stream, media_type)},
            data={"relative_paths": relative_path},
            headers={"Idempotency-Key": f"reference-{uuid.uuid4()}"},
        )
    response.raise_for_status()


def _wait_for_run(
    client: httpx.Client,
    project_id: str,
    run_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}"
        )
        response.raise_for_status()
        payload = response.json()
        status = payload["status"]
        print(f"  {status}: {payload.get('current_step')}", flush=True)
        if status == "requires_input":
            skipped = client.post(
                f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/questions/skip"
            )
            skipped.raise_for_status()
            # Skipping questions resumes an asynchronous analysis run.  Poll it
            # again instead of treating the immediate response as final.
            continue
        if status in TERMINAL_STATUSES:
            return payload
        if time.monotonic() >= deadline:
            raise TimeoutError(f"analysis {run_id} did not finish in time")
        time.sleep(3)


def run_case(
    client: httpx.Client,
    case: dict[str, Any],
    output_root: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    case_id = case["id"]
    specification = ROOT / Path(case["specification_path"])
    reference = ROOT / Path(case["calculated_excel_path"])
    if not specification.is_file() or not reference.is_file():
        raise FileNotFoundError(f"missing fixture for {case_id}")

    print(f"[{case_id}] {case['project_name']}", flush=True)
    created = client.post(
        "/api/v1/projects",
        json={"name": f"Regression — {case['project_name']}"},
    )
    created.raise_for_status()
    project_id = created.json()["id"]
    _upload(
        client,
        project_id,
        case_id,
        specification,
        case["specification_path"],
    )
    started = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs",
        json={"question_policy": "material_only"},
    )
    started.raise_for_status()
    run_id = started.json()["run_id"]
    run = _wait_for_run(client, project_id, run_id, timeout_seconds)

    result: dict[str, Any] = {
        "case_id": case_id,
        "project_id": project_id,
        "run_id": run_id,
        "status": run["status"],
        "analysis_run": run,
    }
    analysis = run.get("result") or {}
    result.update(
        {
            "project_type_code": analysis.get("project_type_code"),
            "confidence": analysis.get("confidence"),
            "prompt_version": analysis.get("prompt_version"),
            "estimated_hours": (analysis.get("work_plan") or {}).get(
                "contract_total_effort_hours"
            )
            or (analysis.get("work_plan") or {}).get("total_effort_hours"),
        }
    )
    if run["status"] != "ready":
        result["errors"] = run.get("errors", [])
        return result

    case_output = output_root / case_id
    case_output.mkdir(parents=True, exist_ok=True)
    workbook_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/report.xlsx",
        timeout=timeout_seconds,
    )
    workbook_response.raise_for_status()
    generated_path = case_output / f"generated-{run_id}.xlsx"
    generated_path.write_bytes(workbook_response.content)
    work_plan = analysis.get("work_plan") or {}
    generated_total = work_plan.get("contract_total_sale_amount_rub")
    missing_contract_term = (
        generated_total is None
        and float(work_plan.get("monthly_effort_hours") or 0) > 0
    )
    if generated_total is None:
        generated_total = work_plan.get("total_sale_amount_rub")
    generated = (
        Metric(float(generated_total), "analysis.result.work_plan contract total")
        if generated_total is not None
        else _generated_metric(generated_path)
    )
    expected = _reference_metric(case, reference)
    result["generated_workbook"] = generated_path.relative_to(ROOT).as_posix()
    result["generated_total_rub_ex_vat"] = generated.value
    if expected is not None:
        if missing_contract_term:
            result.update(
                {
                    "reference_total_rub_ex_vat": expected.value,
                    "reference_metric": expected.description,
                    "comparison_quality": "non_comparable_missing_contract_term",
                    "included_in_accuracy": False,
                    "comparison_note": (
                        "The specification produced monthly work but no evidenced contract term; "
                        "a project-total price cannot be compared safely."
                    ),
                }
            )
            print(
                f"  generated monthly plan={generated.value:,.2f}; reference={expected.value:,.2f}; "
                "not comparable without contract term",
                flush=True,
            )
            return result
        deviation = (generated.value - expected.value) / expected.value
        comparison_quality = case.get("comparison_quality", "comparable")
        result.update(
            {
                "reference_total_rub_ex_vat": expected.value,
                "reference_metric": expected.description,
                "deviation_fraction": deviation,
                "comparison_quality": comparison_quality,
                "included_in_accuracy": comparison_quality == "comparable",
                "within_5_percent": (
                    abs(deviation) <= 0.05
                    if comparison_quality == "comparable"
                    else None
                ),
                "within_20_percent": (
                    abs(deviation) <= 0.2
                    if comparison_quality == "comparable"
                    else None
                ),
            }
        )
        print(
            f"  generated={generated.value:,.2f}; reference={expected.value:,.2f}; "
            f"deviation={deviation:+.2%}",
            flush=True,
        )
    else:
        print(f"  generated={generated.value:,.2f}; reference metric not configured")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run project specifications through the API one by one."
    )
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Recompute comparison fields for existing generated workbooks without API calls.",
    )
    parser.add_argument(
        "--redownload-existing",
        action="store_true",
        help="Download existing run workbooks again, then refresh comparison fields.",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "reference-regression",
    )
    parser.add_argument("--timeout", type=float, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir = args.output_dir.resolve()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    cases = catalog["cases"]
    selected_ids = set(args.case_ids or [])
    if not args.all and not selected_ids:
        raise SystemExit("pass --all or at least one --case-id")
    selected = [case for case in cases if args.all or case["id"] in selected_ids]
    unknown = selected_ids - {case["id"] for case in selected}
    if unknown:
        raise SystemExit(f"unknown case ids: {', '.join(sorted(unknown))}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "results.json"
    previous_results: list[dict[str, Any]] = []
    if report_path.is_file():
        previous_results = json.loads(report_path.read_text(encoding="utf-8"))
    selected_case_ids = {case["id"] for case in selected}
    results = [
        item for item in previous_results if item.get("case_id") not in selected_case_ids
    ]
    if args.redownload_existing:
        by_case_id = {item.get("case_id"): item for item in previous_results}
        with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
            for case in selected:
                item = by_case_id.get(case["id"])
                if item is None or not item.get("generated_workbook"):
                    continue
                response = client.get(
                    f"/api/v1/projects/{item['project_id']}/analysis-runs/{item['run_id']}/report.xlsx",
                    timeout=args.timeout,
                )
                response.raise_for_status()
                (ROOT / item["generated_workbook"]).write_bytes(response.content)
                print(f"[{case['id']}] workbook downloaded again", flush=True)
        args.refresh_existing = True
    if args.refresh_existing:
        by_case_id = {item.get("case_id"): item for item in previous_results}
        refreshed = list(results)
        for case in selected:
            item = by_case_id.get(case["id"])
            if item is None or not item.get("generated_workbook"):
                print(f"[{case['id']}] no existing generated workbook", flush=True)
                continue
            generated = _generated_metric(ROOT / item["generated_workbook"])
            expected = _reference_metric(
                case, ROOT / Path(case["calculated_excel_path"])
            )
            item = dict(item)
            item["generated_total_rub_ex_vat"] = generated.value
            if expected is not None:
                deviation = (generated.value - expected.value) / expected.value
                comparison_quality = case.get("comparison_quality", "comparable")
                item.update(
                    {
                        "reference_total_rub_ex_vat": expected.value,
                        "reference_metric": expected.description,
                        "deviation_fraction": deviation,
                        "comparison_quality": comparison_quality,
                        "included_in_accuracy": comparison_quality == "comparable",
                        "within_5_percent": (
                            abs(deviation) <= 0.05
                            if comparison_quality == "comparable"
                            else None
                        ),
                        "within_20_percent": (
                            abs(deviation) <= 0.2
                            if comparison_quality == "comparable"
                            else None
                        ),
                    }
                )
                print(f"[{case['id']}] deviation={deviation:+.2%}", flush=True)
            if case.get("comparison_note"):
                item["comparison_note"] = case["comparison_note"]
            refreshed.append(item)
        report_path.write_text(
            json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        health_deadline = time.monotonic() + 60
        while True:
            try:
                health = client.get("/health")
                health.raise_for_status()
                break
            except httpx.HTTPError:
                if time.monotonic() >= health_deadline:
                    raise
                time.sleep(2)
        for case in selected:
            try:
                results.append(run_case(client, case, args.output_dir, args.timeout))
            except Exception as error:  # continue to preserve the sequential report
                print(f"  ERROR: {error}", file=sys.stderr, flush=True)
                results.append(
                    {"case_id": case["id"], "status": "runner_error", "error": str(error)}
                )
            report_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return 1 if any(item["status"] not in {"ready"} for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
