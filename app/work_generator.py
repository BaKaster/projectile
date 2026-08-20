from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.stage_contracts import ProjectStagePlan, ResolvedStage, StageCatalog
from app.stage_planner import StagePlanner
from app.work_contracts import (
    GeneratedWorkPlan,
    ProjectSpecificWork,
    StageWorkPackage,
    WorkFact,
    WorkItem,
    WorkPlanContext,
    HoursBasis,
)


class WorkCatalogError(ValueError):
    """The work catalog is invalid or inconsistent with the stage catalog."""


class WorkGenerationError(ValueError):
    """A work plan cannot be generated safely from the supplied context."""


class WorkSignalDefinition(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)


class WorkDefinition(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    inclusion: Literal["always", "conditional", "optional"]
    signals: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(min_length=1)
    estimation_drivers: list[str] = Field(default_factory=list)
    evidence: Literal["company", "industry", "synthesized"]
    hours_basis: HoursBasis | None = None

    @model_validator(mode="after")
    def validate_activation(self) -> WorkDefinition:
        if self.inclusion == "always" and self.signals:
            raise ValueError("always work must not contain signals")
        if self.inclusion != "always" and not self.signals:
            raise ValueError(f"{self.inclusion} work requires signals")
        return self


class StageWorkTemplate(BaseModel):
    stage_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    hours_basis: HoursBasis = "Всего"
    works: list[WorkDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_codes(self) -> StageWorkTemplate:
        codes = [work.code for work in self.works]
        if len(codes) != len(set(codes)):
            raise ValueError(f"duplicate work codes in stage {self.stage_code}")
        return self


class WorkTemplate(BaseModel):
    template_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    stages: list[StageWorkTemplate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_stages(self) -> WorkTemplate:
        codes = [stage.stage_code for stage in self.stages]
        if len(codes) != len(set(codes)):
            raise ValueError(f"duplicate stages in work template {self.template_code}")
        return self


class SpecializationAddition(BaseModel):
    stage_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    works: list[str] = Field(min_length=1)


class ProjectTypeWorkSpecialization(BaseModel):
    project_type_code: str = Field(min_length=1)
    template_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    scope_dimensions: list[str] = Field(default_factory=list)
    additions: list[SpecializationAddition] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    exclude_work_codes: list[str] = Field(default_factory=list)


class WorkCatalog(BaseModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: Literal["active"]
    signal_extensions: list[WorkSignalDefinition] = Field(default_factory=list)
    work_templates: list[WorkTemplate] = Field(min_length=1)
    project_type_specializations: list[ProjectTypeWorkSpecialization] = Field(
        min_length=1
    )


_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_STOP_WORDS = {
    "always",
    "conditional",
    "optional",
    "выполнить",
    "выполнять",
    "работа",
    "работы",
    "проект",
    "проекта",
    "проекту",
    "подготовить",
    "провести",
    "сформировать",
    "настроить",
    "число",
    "количество",
    "требования",
    "результат",
    "результаты",
}


class WorkGenerator:
    def __init__(self, catalog: WorkCatalog, stage_catalog: StageCatalog) -> None:
        self.catalog = catalog
        self.stage_catalog = stage_catalog
        self._templates = {
            template.template_code: template for template in catalog.work_templates
        }
        self._specializations = {
            item.project_type_code: item
            for item in catalog.project_type_specializations
        }
        self._stage_signal_codes = {
            signal.code for signal in stage_catalog.signal_catalog
        }
        self._extension_signal_codes = {
            signal.code for signal in catalog.signal_extensions
        }
        self.signal_codes = self._stage_signal_codes | self._extension_signal_codes
        self._validate_catalog()

    @classmethod
    def from_file(
        cls, work_catalog_path: Path, stage_planner: StagePlanner
    ) -> WorkGenerator:
        data = json.loads(work_catalog_path.read_text(encoding="utf-8"))
        return cls(WorkCatalog.model_validate(data), stage_planner.catalog)

    def generate(
        self,
        stage_plan: ProjectStagePlan,
        context: WorkPlanContext | None = None,
    ) -> GeneratedWorkPlan:
        resolved_context = context or WorkPlanContext()
        specialization = self._specializations.get(stage_plan.project_type_code)
        if specialization is None:
            raise WorkGenerationError(
                f"unknown project type specialization: {stage_plan.project_type_code}"
            )
        if specialization.template_code != stage_plan.template_code:
            raise WorkGenerationError(
                "work specialization and stage plan use different templates"
            )

        active_signals = set(stage_plan.active_signals) | set(resolved_context.signals)
        unknown_signals = active_signals - self.signal_codes
        if unknown_signals:
            raise WorkGenerationError(
                "unknown work signals: " + ", ".join(sorted(unknown_signals))
            )

        template = self._templates[stage_plan.template_code]
        stages = {stage.stage_code: stage for stage in template.stages}
        additions = {
            addition.stage_code: addition.works for addition in specialization.additions
        }
        custom_stage_codes = {
            item.stage_code for item in resolved_context.project_specific_works
        }
        unknown_custom_stages = custom_stage_codes - set(stages)
        if unknown_custom_stages:
            raise WorkGenerationError(
                "project-specific works use unknown stages: "
                + ", ".join(sorted(unknown_custom_stages))
            )

        known_codes = self._known_codes(template, specialization)
        known_codes.update(
            self._custom_work_code(item.stage_code, item.name)
            for item in resolved_context.project_specific_works
        )
        requested_codes = set(resolved_context.include_work_codes) | set(
            resolved_context.exclude_work_codes
        )
        unknown_codes = requested_codes - known_codes
        if unknown_codes:
            raise WorkGenerationError(
                "unknown work codes: " + ", ".join(sorted(unknown_codes))
            )

        packages: list[StageWorkPackage] = []
        duplicate_semantic_codes: list[str] = []
        selected_stages = [
            stage for stage in stage_plan.stages if stage.status == "selected"
        ]
        selected_stage_codes = {stage.code for stage in selected_stages}
        included_outside_selected = {
            code
            for code in resolved_context.include_work_codes
            if code.split(".", 1)[0] not in selected_stage_codes
        }
        if included_outside_selected:
            raise WorkGenerationError(
                "works may only target selected stages: "
                + ", ".join(sorted(included_outside_selected))
            )
        custom_outside_selected = custom_stage_codes - selected_stage_codes
        if custom_outside_selected:
            raise WorkGenerationError(
                "project-specific works may only target selected stages: "
                + ", ".join(sorted(custom_outside_selected))
            )

        custom_by_stage: dict[str, list[ProjectSpecificWork]] = {}
        duplicate_custom_codes: list[str] = []
        seen_custom_codes: set[str] = set()
        for item in resolved_context.project_specific_works:
            custom_code = self._custom_work_code(item.stage_code, item.name)
            if custom_code in seen_custom_codes:
                duplicate_custom_codes.append(custom_code)
                continue
            seen_custom_codes.add(custom_code)
            custom_by_stage.setdefault(item.stage_code, []).append(item)

        for stage in selected_stages:
            stage_template = stages[stage.code]
            works: list[WorkItem] = []
            for definition in stage_template.works:
                work_code = f"{stage.code}.{definition.code}"
                if (
                    work_code in specialization.exclude_work_codes
                    and work_code not in resolved_context.include_work_codes
                ):
                    continue
                matched_signals = sorted(active_signals & set(definition.signals))
                selection_reason = self._selection_reason(
                    work_code,
                    definition.inclusion,
                    matched_signals,
                    resolved_context,
                )
                if selection_reason is None:
                    continue
                works.append(
                    self._build_item(
                        work_code=work_code,
                        name=definition.name,
                        outputs=definition.outputs,
                        estimation_drivers=definition.estimation_drivers,
                        selection_reason=selection_reason,
                        matched_signals=matched_signals,
                        facts=resolved_context.facts,
                        scope_dimensions=specialization.scope_dimensions,
                        stage=stage,
                        hours_basis=definition.hours_basis or stage_template.hours_basis,
                    )
                )

            for name in additions.get(stage.code, []):
                work_code = self._special_work_code(stage.code, name)
                if work_code in resolved_context.exclude_work_codes:
                    continue
                works.append(
                    self._build_item(
                        work_code=work_code,
                        name=name,
                        outputs=stage.deliverables,
                        estimation_drivers=stage.work_generation.estimation_drivers,
                        selection_reason=(
                            "Работа добавлена специализацией выбранного типа проекта."
                        ),
                        matched_signals=[],
                        facts=resolved_context.facts,
                        scope_dimensions=specialization.scope_dimensions,
                        stage=stage,
                        hours_basis=stage_template.hours_basis,
                    )
                )

            for custom in custom_by_stage.get(stage.code, []):
                work_code = self._custom_work_code(stage.code, custom.name)
                if work_code in resolved_context.exclude_work_codes:
                    continue
                works.append(
                    self._build_item(
                        work_code=work_code,
                        name=custom.name,
                        outputs=custom.outputs,
                        estimation_drivers=custom.estimation_drivers,
                        selection_reason=(
                            "Проектно-специфичная работа: " + custom.rationale
                        ),
                        matched_signals=[],
                        facts=resolved_context.facts,
                        scope_dimensions=specialization.scope_dimensions,
                        stage=stage,
                        hours_basis=custom.hours_basis or stage_template.hours_basis,
                        extra_source_document_ids=custom.source_document_ids,
                    )
                )

            works, removed_codes = self._deduplicate_works(works)
            duplicate_semantic_codes.extend(removed_codes)

            if not works:
                raise WorkGenerationError(
                    f"selected stage has no applicable works: {stage.code}"
                )
            packages.append(StageWorkPackage(stage_code=stage.code, works=works))

        warnings = [
            (
                "Трудозатраты и роли не рассчитывались: каталог определяет состав "
                "работ и драйверы, а оценка выполняется после подтверждения объёмов."
            )
        ]
        warnings.extend(
            f"Не включать без явного scope: {item}"
            for item in specialization.exclusions
        )
        if duplicate_custom_codes:
            warnings.append(
                "Повторные проектно-специфичные работы объединены: "
                + ", ".join(sorted(set(duplicate_custom_codes)))
            )
        if duplicate_semantic_codes:
            warnings.append(
                "Семантически повторяющиеся работы этапа объединены: "
                + ", ".join(sorted(set(duplicate_semantic_codes)))
            )
        result = GeneratedWorkPlan(
            project_type_code=stage_plan.project_type_code,
            stage_schema_version=stage_plan.schema_version,
            work_catalog_version=self.catalog.schema_version,
            packages=packages,
            warnings=warnings,
        )
        result.validate_against(stage_plan)
        return result

    def signal_descriptions(self) -> dict[str, str]:
        descriptions = {
            item.code: item.description for item in self.stage_catalog.signal_catalog
        }
        descriptions.update(
            {item.code: item.description for item in self.catalog.signal_extensions}
        )
        return descriptions

    def specialization_codes(self) -> list[str]:
        return sorted(self._specializations)

    def prompt_context(self) -> dict[str, object]:
        """Return production-safe knowledge for finding non-template project work."""
        return {
            "templates": {
                template.template_code: [
                    {
                        "stage_code": stage.stage_code,
                        "typical_work_names": [work.name for work in stage.works],
                    }
                    for stage in template.stages
                ]
                for template in self.catalog.work_templates
            },
            "project_types": {
                item.project_type_code: {
                    "template_code": item.template_code,
                    "scope_dimensions": item.scope_dimensions,
                    "typical_additions": [
                        {
                            "stage_code": addition.stage_code,
                            "work_names": addition.works,
                        }
                        for addition in item.additions
                    ],
                    "exclusions": item.exclusions,
                }
                for item in self.catalog.project_type_specializations
            },
        }

    def _selection_reason(
        self,
        work_code: str,
        inclusion: str,
        matched_signals: list[str],
        context: WorkPlanContext,
    ) -> str | None:
        if work_code in context.exclude_work_codes:
            return None
        if work_code in context.include_work_codes:
            return "Работа включена явным решением пользователя или эксперта."
        if inclusion == "always":
            return "Типовая обязательная работа выбранного этапа."
        if matched_signals:
            return "Работа активирована фактами проекта через сигналы: " + ", ".join(
                matched_signals
            )
        return None

    def _build_item(
        self,
        *,
        work_code: str,
        name: str,
        outputs: list[str],
        estimation_drivers: list[str],
        selection_reason: str,
        matched_signals: list[str],
        facts: list[WorkFact],
        scope_dimensions: list[str],
        stage: ResolvedStage,
        hours_basis: HoursBasis,
        extra_source_document_ids: list[str] | None = None,
    ) -> WorkItem:
        relevant = self._relevant_facts(
            facts,
            [
                name,
                *outputs,
                *estimation_drivers,
                *scope_dimensions,
                *stage.work_generation.required_inputs,
            ],
        )
        context_facts = [
            f"{fact.name}: {self._shorten(fact.value)}" for fact in relevant
        ]
        description = name.rstrip(".") + "."
        if outputs:
            description += " Проверяемый результат: " + "; ".join(outputs) + "."
        assumptions: list[str] = []
        if estimation_drivers and not relevant:
            assumptions.append(
                "До оценки трудозатрат подтвердить драйверы: "
                + ", ".join(estimation_drivers)
                + "."
            )
        source_document_ids = sorted(
            {
                document_id
                for fact in relevant
                for document_id in fact.source_document_ids
            }
            | set(extra_source_document_ids or [])
        )
        return WorkItem(
            work_code=work_code,
            name=name,
            description=description,
            source_document_ids=source_document_ids,
            assumptions=assumptions,
            outputs=outputs,
            estimation_drivers=estimation_drivers,
            selection_reason=selection_reason,
            matched_signals=matched_signals,
            context_facts=context_facts,
            hours_basis=hours_basis,
        )

    @classmethod
    def _deduplicate_works(
        cls, works: list[WorkItem]
    ) -> tuple[list[WorkItem], list[str]]:
        """Keep the first canonical work when generated sources describe the same task."""
        kept: list[WorkItem] = []
        removed: list[str] = []
        for candidate in works:
            candidate_tokens = cls._tokens(candidate.name)
            duplicate = False
            for existing in kept:
                existing_tokens = cls._tokens(existing.name)
                if not candidate_tokens or not existing_tokens:
                    continue
                intersection = len(candidate_tokens & existing_tokens)
                union = len(candidate_tokens | existing_tokens)
                shorter = min(len(candidate_tokens), len(existing_tokens))
                if candidate_tokens == existing_tokens or (
                    shorter >= 4
                    and intersection / shorter >= 0.8
                    and intersection / union >= 0.65
                ):
                    existing.source_document_ids = sorted(
                        set(existing.source_document_ids) | set(candidate.source_document_ids)
                    )
                    existing.assumptions = list(
                        dict.fromkeys([*existing.assumptions, *candidate.assumptions])
                    )
                    removed.append(candidate.work_code)
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)
        return kept, removed

    def _relevant_facts(
        self, facts: list[WorkFact], phrases: list[str]
    ) -> list[WorkFact]:
        target_tokens = self._tokens(" ".join(phrases))
        scored: list[tuple[int, int, WorkFact]] = []
        for index, fact in enumerate(facts):
            fact_tokens = self._tokens(f"{fact.name} {fact.value}")
            score = len(target_tokens & fact_tokens)
            if score:
                scored.append((score, -index, fact))
        scored.sort(reverse=True, key=lambda row: (row[0], row[1]))
        return [row[2] for row in scored[:3]]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        result: set[str] = set()
        for token in _TOKEN_RE.findall(text.casefold()):
            if token in _STOP_WORDS or len(token) < 4:
                continue
            result.add(token[:5] if len(token) >= 7 else token)
        return result

    @staticmethod
    def _shorten(value: str, limit: int = 180) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

    @staticmethod
    def _special_work_code(stage_code: str, name: str) -> str:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        return f"{stage_code}.special.{digest}"

    @staticmethod
    def _custom_work_code(stage_code: str, name: str) -> str:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        return f"{stage_code}.custom.{digest}"

    def _known_codes(
        self,
        template: WorkTemplate,
        specialization: ProjectTypeWorkSpecialization,
    ) -> set[str]:
        codes = {
            f"{stage.stage_code}.{work.code}"
            for stage in template.stages
            for work in stage.works
        }
        codes.update(
            self._special_work_code(addition.stage_code, name)
            for addition in specialization.additions
            for name in addition.works
        )
        return codes

    def _validate_catalog(self) -> None:
        if len(self._templates) != len(self.catalog.work_templates):
            raise WorkCatalogError("work catalog contains duplicate template codes")
        if len(self._specializations) != len(self.catalog.project_type_specializations):
            raise WorkCatalogError(
                "work catalog contains duplicate project-type specializations"
            )
        if self._stage_signal_codes & self._extension_signal_codes:
            raise WorkCatalogError("work signal extensions duplicate stage signals")

        stage_templates = {
            template.code: {stage.code for stage in template.stages}
            for template in self.stage_catalog.templates
        }
        if set(stage_templates) != set(self._templates):
            raise WorkCatalogError("work and stage template coverage differs")
        for template_code, work_template in self._templates.items():
            work_stage_codes = {stage.stage_code for stage in work_template.stages}
            if work_stage_codes != stage_templates[template_code]:
                raise WorkCatalogError(
                    f"work stage coverage differs for template {template_code}"
                )
            for stage in work_template.stages:
                for work in stage.works:
                    unknown = set(work.signals) - self.signal_codes
                    if unknown:
                        raise WorkCatalogError(
                            f"work {stage.stage_code}.{work.code} uses unknown signals: "
                            + ", ".join(sorted(unknown))
                        )

        stage_profiles = {
            profile.project_type_code: profile.template_code
            for profile in self.stage_catalog.project_type_profiles
        }
        if set(stage_profiles) != set(self._specializations):
            raise WorkCatalogError("work specialization coverage differs")
        for project_type_code, specialization in self._specializations.items():
            if specialization.template_code != stage_profiles[project_type_code]:
                raise WorkCatalogError(
                    f"template mismatch for specialization {project_type_code}"
                )
            known_stages = stage_templates[specialization.template_code]
            unknown_stages = {
                addition.stage_code
                for addition in specialization.additions
                if addition.stage_code not in known_stages
            }
            if unknown_stages:
                raise WorkCatalogError(
                    f"specialization {project_type_code} uses unknown stages: "
                    + ", ".join(sorted(unknown_stages))
                )
            unknown_exclusions = set(specialization.exclude_work_codes) - self._known_codes(
                self._templates[specialization.template_code], specialization
            )
            if unknown_exclusions:
                raise WorkCatalogError(
                    f"specialization {project_type_code} excludes unknown works: "
                    + ", ".join(sorted(unknown_exclusions))
                )
