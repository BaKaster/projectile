from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.stage_contracts import ProjectStagePlan


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

    @model_validator(mode="after")
    def validate_estimate_pair(self) -> WorkItem:
        if (self.estimate_method is None) != (self.effort_hours is None):
            raise ValueError(
                "estimate_method and effort_hours must be set or omitted together"
            )
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


class WorkPlanContext(BaseModel):
    signals: list[str] = Field(default_factory=list)
    facts: list[WorkFact] = Field(default_factory=list)
    project_specific_works: list[ProjectSpecificWork] = Field(default_factory=list)
    include_work_codes: list[str] = Field(default_factory=list)
    exclude_work_codes: list[str] = Field(default_factory=list)

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
