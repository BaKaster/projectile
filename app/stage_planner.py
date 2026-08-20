from __future__ import annotations

import json
from pathlib import Path

from app.stage_contracts import (
    ProjectStagePlan,
    ProjectTypeProfile,
    ResolvedStage,
    StageCatalog,
    StageDefinition,
    StagePlanContext,
)


class StageCatalogError(ValueError):
    """The stage catalog and the project-type catalog are inconsistent."""


class StagePlanningError(ValueError):
    """The requested stage plan cannot be constructed safely."""


class StagePlanner:
    def __init__(
        self,
        stage_catalog: StageCatalog,
        project_type_names: dict[str, str],
    ) -> None:
        self.catalog = stage_catalog
        self.project_type_names = project_type_names
        self._templates = {item.code: item for item in stage_catalog.templates}
        self._profiles = {
            item.project_type_code: item
            for item in stage_catalog.project_type_profiles
        }
        self._signal_codes = {item.code for item in stage_catalog.signal_catalog}
        self._validate_catalog()

    @classmethod
    def from_files(
        cls, project_types_path: Path, stage_catalog_path: Path
    ) -> StagePlanner:
        project_types_data = json.loads(
            project_types_path.read_text(encoding="utf-8")
        )
        stage_data = json.loads(stage_catalog_path.read_text(encoding="utf-8"))
        project_type_names = {
            item["code"]: item["name"]
            for direction in project_types_data["directions"]
            for item in direction["project_types"]
        }
        return cls(StageCatalog.model_validate(stage_data), project_type_names)

    def build_plan(
        self,
        project_type_code: str,
        context: StagePlanContext | None = None,
    ) -> ProjectStagePlan:
        resolved_context = context or StagePlanContext()
        profile = self._profiles.get(project_type_code)
        if profile is None:
            raise StagePlanningError(f"unknown project type: {project_type_code}")
        template = self._templates[profile.template_code]
        active_signals = set(profile.default_signals) | set(resolved_context.signals)
        unknown_signals = active_signals - self._signal_codes
        if unknown_signals:
            raise StagePlanningError(
                "unknown project signals: " + ", ".join(sorted(unknown_signals))
            )

        template_stage_codes = {stage.code for stage in template.stages}
        requested_stage_codes = set(resolved_context.include_stage_codes) | set(
            resolved_context.exclude_stage_codes
        )
        unknown_stages = requested_stage_codes - template_stage_codes
        if unknown_stages:
            raise StagePlanningError(
                "stages do not belong to the selected template: "
                + ", ".join(sorted(unknown_stages))
            )

        warnings: list[str] = []
        stages: list[ResolvedStage] = []
        for base_stage in template.stages:
            stage = self._apply_override(base_stage, profile)
            matched = sorted(active_signals & set(stage.applicability.signals))
            explicitly_included = stage.code in resolved_context.include_stage_codes
            explicitly_excluded = stage.code in resolved_context.exclude_stage_codes

            if explicitly_excluded:
                if stage.applicability.mode == "always":
                    raise StagePlanningError(
                        f"required stage cannot be excluded: {stage.code}"
                    )
                continue
            if explicitly_included:
                status = "selected"
                reason = "Этап включён явным решением пользователя или эксперта."
            elif stage.applicability.mode == "always":
                status = "selected"
                reason = "Обязательный этап выбранного шаблона."
            elif stage.applicability.mode == "if_any" and matched:
                status = "selected"
                reason = "Активирован хотя бы один проектный сигнал."
            elif stage.applicability.mode == "if_all" and set(
                stage.applicability.signals
            ).issubset(active_signals):
                status = "selected"
                reason = "Активированы все обязательные проектные сигналы."
            else:
                status = "candidate"
                reason = (
                    "Недостаточно подтверждений; этап сохранён кандидатом для "
                    "проверки, а не удалён автоматически."
                )
                warnings.append(
                    f"Подтвердите применимость этапа {stage.code}: {stage.name}"
                )

            if status == "candidate" and not resolved_context.include_candidates:
                continue
            stages.append(
                ResolvedStage(
                    **stage.model_dump(mode="python"),
                    status=status,
                    selection_reason=reason,
                    matched_signals=matched,
                )
            )

        return ProjectStagePlan(
            schema_version=self.catalog.schema_version,
            methodology_version=self.catalog.methodology_version,
            project_type_code=project_type_code,
            project_type_name=self.project_type_names[project_type_code],
            template_code=template.code,
            profile_name=profile.profile_name,
            specialized_scope=profile.specialized_scope,
            active_signals=sorted(active_signals),
            work_generation_hints=profile.work_generation_hints,
            warnings=warnings,
            stages=stages,
        )

    def profile_codes(self) -> list[str]:
        return sorted(self._profiles)

    def _apply_override(
        self, stage: StageDefinition, profile: ProjectTypeProfile
    ) -> StageDefinition:
        override = profile.stage_overrides.get(stage.code)
        if override is None:
            return stage
        values = override.model_dump(exclude_none=True, mode="python")
        return stage.model_copy(update=values)

    def _validate_catalog(self) -> None:
        duplicate_signals = len(self._signal_codes) != len(self.catalog.signal_catalog)
        if duplicate_signals:
            raise StageCatalogError("signal catalog contains duplicate codes")
        if len(self._templates) != len(self.catalog.templates):
            raise StageCatalogError("stage catalog contains duplicate template codes")
        if len(self._profiles) != len(self.catalog.project_type_profiles):
            raise StageCatalogError("stage catalog contains duplicate project profiles")

        project_codes = set(self.project_type_names)
        profile_codes = set(self._profiles)
        if project_codes != profile_codes:
            missing = sorted(project_codes - profile_codes)
            unknown = sorted(profile_codes - project_codes)
            raise StageCatalogError(
                f"project profile coverage mismatch; missing={missing}, unknown={unknown}"
            )

        for template in self.catalog.templates:
            for stage in template.stages:
                unknown = set(stage.applicability.signals) - self._signal_codes
                if unknown:
                    raise StageCatalogError(
                        f"stage {template.code}.{stage.code} uses unknown signals: "
                        + ", ".join(sorted(unknown))
                    )
        for profile in self.catalog.project_type_profiles:
            if profile.template_code not in self._templates:
                raise StageCatalogError(
                    f"profile {profile.project_type_code} uses unknown template "
                    f"{profile.template_code}"
                )
            unknown_signals = set(profile.default_signals) - self._signal_codes
            if unknown_signals:
                raise StageCatalogError(
                    f"profile {profile.project_type_code} uses unknown signals: "
                    + ", ".join(sorted(unknown_signals))
                )
            stage_codes = {
                stage.code for stage in self._templates[profile.template_code].stages
            }
            unknown_overrides = set(profile.stage_overrides) - stage_codes
            if unknown_overrides:
                raise StageCatalogError(
                    f"profile {profile.project_type_code} overrides unknown stages: "
                    + ", ".join(sorted(unknown_overrides))
                )
