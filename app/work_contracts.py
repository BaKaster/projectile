from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.stage_contracts import ProjectStagePlan

HoursBasis = Literal["Всего", "В месяц", "На единицу", "Ед. × месяц"]


class RoleAssignment(BaseModel):
    role_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    role_name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    effort_hours: float = Field(gt=0)
    sale_rate_rub_per_hour: float | None = Field(default=None, ge=0)
    cost_rate_rub_per_hour: float | None = Field(default=None, ge=0)
    sale_amount_rub: float | None = Field(default=None, ge=0)
    cost_amount_rub: float | None = Field(default=None, ge=0)
    confidence: Literal["low", "medium", "high"] = "low"
    rationale: str = Field(min_length=1)


class WorkItem(BaseModel):
    work_code: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    role_code: str = Field(default="unassigned", min_length=1)
    estimate_method: Literal["norm", "parametric", "analogy", "expert"] | None = None
    effort_hours: float | None = Field(default=None, ge=0)
    source_document_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    estimation_drivers: list[str] = Field(default_factory=list)
    selection_reason: str = Field(default="legacy_or_external_generator", min_length=1)
    matched_signals: list[str] = Field(default_factory=list)
    context_facts: list[str] = Field(default_factory=list)
    role_assignments: list[RoleAssignment] = Field(default_factory=list)
    effort_min_hours: float | None = Field(default=None, ge=0)
    effort_max_hours: float | None = Field(default=None, ge=0)
    hours_basis: HoursBasis = "Всего"

    @model_validator(mode="after")
    def validate_estimate_pair(self) -> WorkItem:
        if (self.estimate_method is None) != (self.effort_hours is None):
            raise ValueError(
                "estimate_method and effort_hours must be set or omitted together"
            )
        if self.effort_hours is not None and (
            self.role_assignments
            or self.effort_min_hours is not None
            or self.effort_max_hours is not None
        ):
            if self.effort_min_hours is None or self.effort_max_hours is None:
                raise ValueError("estimated work must include an effort range")
            if not self.effort_min_hours <= self.effort_hours <= self.effort_max_hours:
                raise ValueError("effort_hours must be inside the effort range")
            assigned = sum(item.effort_hours for item in self.role_assignments)
            if self.role_assignments and abs(assigned - self.effort_hours) > 0.011:
                raise ValueError("effort_hours must equal the role assignment total")
        return self


class WorkFact(BaseModel):
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list)


class ProjectSpecificWork(BaseModel):
    stage_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    outputs: list[str] = Field(min_length=1)
    estimation_drivers: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(min_length=1)
    hours_basis: HoursBasis | None = None


class WorkPlanContext(BaseModel):
    signals: list[str] = Field(default_factory=list)
    facts: list[WorkFact] = Field(default_factory=list)
    project_specific_works: list[ProjectSpecificWork] = Field(default_factory=list)
    include_work_codes: list[str] = Field(default_factory=list)
    exclude_work_codes: list[str] = Field(default_factory=list)
    scope_mode: Literal["baseline", "confirmed_only"] = "baseline"

    @model_validator(mode="after")
    def validate_explicit_selection(self) -> WorkPlanContext:
        overlap = set(self.include_work_codes) & set(self.exclude_work_codes)
        if overlap:
            raise ValueError(
                "the same work cannot be included and excluded: "
                + ", ".join(sorted(overlap))
            )
        return self


class StageWorkPackage(BaseModel):
    stage_code: str = Field(min_length=1)
    works: list[WorkItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_work_codes(self) -> StageWorkPackage:
        codes = [item.work_code for item in self.works]
        if len(codes) != len(set(codes)):
            raise ValueError(f"duplicate work codes in stage {self.stage_code}")
        return self


class GeneratedWorkPlan(BaseModel):
    project_type_code: str
    stage_schema_version: str
    work_catalog_version: str | None = None
    packages: list[StageWorkPackage]
    warnings: list[str] = Field(default_factory=list)
    estimation_version: str | None = None
    estimation_mode: Literal["not_estimated", "deterministic", "ai_refined", "ai_direct"] = "not_estimated"
    total_effort_hours: float | None = Field(default=None, ge=0)
    total_sale_amount_rub: float | None = Field(default=None, ge=0)
    total_cost_amount_rub: float | None = Field(default=None, ge=0)
    contract_months: float | None = Field(default=None, gt=0, le=120)
    contract_months_evidence: list[str] = Field(default_factory=list)
    one_time_effort_hours: float | None = Field(default=None, ge=0)
    monthly_effort_hours: float | None = Field(default=None, ge=0)
    contract_total_effort_hours: float | None = Field(default=None, ge=0)
    one_time_sale_amount_rub: float | None = Field(default=None, ge=0)
    monthly_sale_amount_rub: float | None = Field(default=None, ge=0)
    contract_total_sale_amount_rub: float | None = Field(default=None, ge=0)
    one_time_cost_amount_rub: float | None = Field(default=None, ge=0)
    monthly_cost_amount_rub: float | None = Field(default=None, ge=0)
    contract_total_cost_amount_rub: float | None = Field(default=None, ge=0)

    def validate_against(
        self,
        stage_plan: ProjectStagePlan,
        *,
        require_all_selected_stages: bool = True,
    ) -> None:
        if self.project_type_code != stage_plan.project_type_code:
            raise ValueError("work plan and stage plan use different project types")
        if self.stage_schema_version != stage_plan.schema_version:
            raise ValueError("work plan was generated for another stage schema version")
        package_codes = [package.stage_code for package in self.packages]
        if len(package_codes) != len(set(package_codes)):
            raise ValueError("work plan contains duplicate stage packages")
        unknown = set(package_codes) - stage_plan.selected_stage_codes
        if unknown:
            raise ValueError(
                "works may only target selected stages: " + ", ".join(sorted(unknown))
            )
        if require_all_selected_stages:
            missing = stage_plan.selected_stage_codes - set(package_codes)
            if missing:
                raise ValueError(
                    "selected stages without work packages: "
                    + ", ".join(sorted(missing))
                )
