from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

StageCode = str
StageStatus = Literal["selected", "candidate"]
ExecutionMode = Literal["sequential", "parallel", "recurring"]
PhaseKind = Literal["delivery", "governance", "recurring"]


class SignalDefinition(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ActivationRule(BaseModel):
    mode: Literal["always", "if_any", "if_all"] = "always"
    signals: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_signals(self) -> ActivationRule:
        if self.mode == "always" and self.signals:
            raise ValueError("always activation must not contain signals")
        if self.mode != "always" and not self.signals:
            raise ValueError(f"{self.mode} activation requires at least one signal")
        return self


class ExitGate(BaseModel):
    name: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    decision: Literal["approve", "go_no_go", "accept", "renew_or_exit"]


class WorkGenerationRule(BaseModel):
    required_inputs: list[str] = Field(min_length=1)
    work_categories: list[str] = Field(min_length=1)
    decomposition_rule: str = Field(min_length=1)
    estimation_drivers: list[str] = Field(default_factory=list)


class StageDefinition(BaseModel):
    code: StageCode = Field(pattern=r"^[a-z][a-z0-9_]*$")
    order: int = Field(gt=0)
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    phase_kind: PhaseKind = "delivery"
    execution_mode: ExecutionMode = "sequential"
    applicability: ActivationRule = Field(default_factory=ActivationRule)
    entry_criteria: list[str] = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)
    exit_gate: ExitGate
    work_generation: WorkGenerationRule


class StageTemplate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    stages: list[StageDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_stage_identity(self) -> StageTemplate:
        codes = [stage.code for stage in self.stages]
        orders = [stage.order for stage in self.stages]
        if len(codes) != len(set(codes)):
            raise ValueError(f"template {self.code} has duplicate stage codes")
        if len(orders) != len(set(orders)):
            raise ValueError(f"template {self.code} has duplicate stage orders")
        if orders != sorted(orders):
            raise ValueError(f"template {self.code} stages must be ordered")
        return self


class StageOverride(BaseModel):
    name: str | None = None
    objective: str | None = None
    entry_criteria: list[str] | None = None
    deliverables: list[str] | None = None
    exit_gate: ExitGate | None = None
    work_generation: WorkGenerationRule | None = None
    applicability: ActivationRule | None = None


class ProjectTypeProfile(BaseModel):
    project_type_code: str = Field(min_length=1)
    template_code: str = Field(min_length=1)
    profile_name: str = Field(min_length=1)
    specialized_scope: str = Field(min_length=1)
    default_signals: list[str] = Field(default_factory=list)
    work_generation_hints: list[str] = Field(min_length=1)
    stage_overrides: dict[str, StageOverride] = Field(default_factory=dict)


class StageCatalog(BaseModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    methodology_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    signal_catalog: list[SignalDefinition] = Field(min_length=1)
    templates: list[StageTemplate] = Field(min_length=1)
    project_type_profiles: list[ProjectTypeProfile] = Field(min_length=1)


class StagePlanContext(BaseModel):
    signals: list[str] = Field(default_factory=list)
    include_stage_codes: list[str] = Field(default_factory=list)
    exclude_stage_codes: list[str] = Field(default_factory=list)
    include_candidates: bool = True

    @model_validator(mode="after")
    def validate_explicit_selection(self) -> StagePlanContext:
        overlap = set(self.include_stage_codes) & set(self.exclude_stage_codes)
        if overlap:
            raise ValueError(
                "the same stage cannot be included and excluded: "
                + ", ".join(sorted(overlap))
            )
        return self


class ResolvedStage(StageDefinition):
    status: StageStatus
    selection_reason: str
    matched_signals: list[str] = Field(default_factory=list)


class ProjectStagePlan(BaseModel):
    schema_version: str
    methodology_version: str
    project_type_code: str
    project_type_name: str
    template_code: str
    profile_name: str
    specialized_scope: str
    active_signals: list[str]
    work_generation_hints: list[str]
    warnings: list[str] = Field(default_factory=list)
    stages: list[ResolvedStage]

    @property
    def selected_stage_codes(self) -> set[str]:
        return {stage.code for stage in self.stages if stage.status == "selected"}
