from __future__ import annotations

import json
import math
import posixpath
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar, Literal
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_CELL_RE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
_NUMBER_IN_TEXT_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)")
_WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

HoursBasis = Literal["Авто", "Всего", "В месяц", "На единицу", "Ед. × месяц"]
EstimateMode = Literal["Бюджетная", "Уточнённая"]
ExternalCategory = Literal[
    "Оборудование",
    "ПО",
    "Лицензии",
    "Облако",
    "Подряд",
    "Логистика",
    "Командировки",
    "Прочее",
]
ExternalPeriodicity = Literal["Разово", "В месяц"]
AssumptionType = Literal["Допущение", "Открытый вопрос", "Ограничение", "Риск"]


class _ExcelInputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class TypeParameterValue(_ExcelInputModel):
    slot_number: int | None = Field(default=None, ge=1, le=8)
    influence_code: str | None = Field(default=None, min_length=1, max_length=50)
    parameter_name: str | None = Field(default=None, min_length=1, max_length=300)
    value: str | float | int

    @model_validator(mode="after")
    def require_selector(self) -> TypeParameterValue:
        if (
            self.slot_number is None
            and self.influence_code is None
            and self.parameter_name is None
        ):
            raise ValueError(
                "type parameter requires slot_number, influence_code, or parameter_name"
            )
        return self


class ExcelRoleAssignment(_ExcelInputModel):
    role: str = Field(min_length=1, max_length=300)
    estimated_hours: float = Field(gt=0)
    sale_rate_override: float | None = Field(default=None, ge=0)
    cost_rate_override: float | None = Field(default=None, ge=0)
    responsibility: str | None = Field(default=None, max_length=1000)


class ExcelWorkItem(_ExcelInputModel):
    stage_no: int = Field(ge=1, le=20)
    stage_name: str = Field(min_length=1, max_length=300)
    work_no: str | int
    work_name: str = Field(min_length=1, max_length=1000)
    role: str | None = Field(default=None, min_length=1, max_length=300)
    estimated_hours: float | None = Field(default=None, gt=0)
    role_assignments: list[ExcelRoleAssignment] = Field(
        default_factory=list, max_length=100
    )
    hours_basis: HoursBasis = "Авто"
    quantity_override: float | None = Field(default=None, gt=0)
    sale_rate_override: float | None = Field(default=None, ge=0)
    cost_rate_override: float | None = Field(default=None, ge=0)
    site_or_contour: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_role_shape(self) -> ExcelWorkItem:
        if isinstance(self.work_no, str) and not self.work_no.strip():
            raise ValueError("work_no must not be blank")
        has_flat = self.role is not None or self.estimated_hours is not None
        if self.role_assignments and has_flat:
            raise ValueError(
                "use either role_assignments or the role/estimated_hours pair"
            )
        if self.role_assignments:
            return self
        if self.role is None or self.estimated_hours is None:
            raise ValueError(
                "work item requires role and estimated_hours or role_assignments"
            )
        return self


class ExcelExternalCost(_ExcelInputModel):
    category: ExternalCategory
    description: str = Field(min_length=1, max_length=1000)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=100)
    unit_cost: float = Field(ge=0)
    unit_sale_price_override: float | None = Field(default=None, ge=0)
    periodicity: ExternalPeriodicity = "Разово"
    comment: str | None = Field(default=None, max_length=2000)


class ExcelAssumption(_ExcelInputModel):
    type: AssumptionType
    text: str = Field(min_length=1, max_length=2000)
    source: str | None = Field(default=None, max_length=1000)
    impact: str | None = Field(default=None, max_length=1000)


class ExcelEstimateRequest(_ExcelInputModel):
    project_name: str = Field(min_length=1, max_length=300)
    project_type_code: str = Field(min_length=1, max_length=100)
    estimate_date: date = Field(default_factory=date.today)
    estimate_mode: EstimateMode = "Бюджетная"
    confidence: float = Field(default=0.7, ge=0, le=1)
    vat_rate: float = Field(default=0.2, ge=0, le=1)
    discount_rate: float = Field(default=0, ge=0, le=1)
    work_hours_per_day: float = Field(default=8, gt=0, le=24)
    planned_start_date: date | None = None
    default_hours_basis: HoursBasis = "Авто"
    source_or_spec_version: str | None = Field(default=None, max_length=1000)
    main_assumption: str | None = Field(default=None, max_length=2000)
    commercial_reserve_rate: float = Field(default=0, ge=0, le=1)
    type_parameters: list[TypeParameterValue] = Field(
        default_factory=list, max_length=8
    )
    work_items: list[ExcelWorkItem] = Field(min_length=1, max_length=100)
    external_costs: list[ExcelExternalCost] = Field(default_factory=list, max_length=20)
    assumptions: list[ExcelAssumption] = Field(default_factory=list, max_length=4)


class TypeParameterDefinition(BaseModel):
    slot_number: int
    parameter_name: str
    unit: str | None
    default_value: str | float | int | None
    required: bool
    influence_code: str
    effect_description: str | None


@dataclass(frozen=True, slots=True)
class _RoleDefinition:
    external_code: str
    exact_text: str


@dataclass(frozen=True, slots=True)
class _ExpandedWorkRow:
    stage_no: int
    stage_name: str
    work_no: str | int
    work_name: str
    role: str
    estimated_hours: float
    hours_basis: HoursBasis
    quantity_override: float | None
    sale_rate_override: float | None
    cost_rate_override: float | None
    site_or_contour: str | None
    comment: str | None


class ExcelEstimateError(ValueError):
    """The estimate cannot be safely mapped to the production workbook."""


class ExcelEstimateService:
    """Populate only the documented OOXML input cells in a clean workbook copy."""

    REQUIRED_SHEETS: ClassVar[set[str]] = {
        "Итого по проекту",
        "Ввод",
        "Расчёт",
        "Внешние затраты",
        "Справочник типов",
        "Параметры типов",
        "Справочник ролей",
        "Проверки",
    }

    def __init__(
        self,
        template_path: Path,
        *,
        role_catalog_path: Path | None = None,
        recalculation_command: str | None = None,
        recalculation_timeout_seconds: int = 120,
    ) -> None:
        self.template_path = template_path
        self._template = template_path.read_bytes()
        package = _WorkbookPackage(self._template)
        missing = self.REQUIRED_SHEETS - set(package.sheet_paths)
        if missing:
            raise ExcelEstimateError(
                "Excel template is missing sheets: " + ", ".join(sorted(missing))
            )
        project_type_rows = package.read_rows("Справочник типов", 2, 27, 1, 9)
        self._project_type_codes = {
            str(row[0]) for row in project_type_rows if row[0] not in (None, "")
        }
        self._default_hours_basis = {
            str(row[0]): str(row[8])
            for row in project_type_rows
            if row[0] not in (None, "") and row[8] not in (None, "")
        }
        self._parameters = self._load_parameters(package)
        self._roles = self._load_roles(package)
        self._internal_role_codes = self._load_internal_role_codes(role_catalog_path)
        self._validate_template_compatibility(package)
        self._template_formula_fingerprint = package.formula_fingerprint()
        self._recalculation_command = recalculation_command
        self._recalculation_timeout_seconds = recalculation_timeout_seconds

    @staticmethod
    def _load_parameters(
        package: _WorkbookPackage,
    ) -> dict[str, list[TypeParameterDefinition]]:
        result: dict[str, list[TypeParameterDefinition]] = {}
        rows = package.read_rows("Параметры типов", 2, 209, 1, 8)
        for row in rows:
            if row[0] in (None, ""):
                continue
            definition = TypeParameterDefinition(
                slot_number=int(row[1]),
                parameter_name=str(row[2]),
                unit=None if row[3] in (None, "") else str(row[3]),
                default_value=row[4],
                required=str(row[5]).strip().casefold() == "да",
                influence_code=str(row[6]).strip(),
                effect_description=None if row[7] in (None, "") else str(row[7]),
            )
            result.setdefault(str(row[0]), []).append(definition)
        return result

    @staticmethod
    def _load_roles(package: _WorkbookPackage) -> list[_RoleDefinition]:
        roles = []
        for row in package.read_rows("Справочник ролей", 2, 20, 1, 3):
            if row[0] in (None, "") or row[2] in (None, ""):
                continue
            roles.append(_RoleDefinition(str(row[0]), str(row[2])))
        return roles

    @staticmethod
    def _load_internal_role_codes(role_catalog_path: Path | None) -> dict[str, str]:
        if role_catalog_path is None:
            return {}
        catalog = json.loads(role_catalog_path.read_text(encoding="utf-8"))
        return {
            str(role["code"]): str(role["external_id"]) for role in catalog["roles"]
        }

    def parameter_definitions(
        self, project_type_code: str
    ) -> list[TypeParameterDefinition]:
        if project_type_code not in self._project_type_codes:
            raise ExcelEstimateError(f"unknown project_type_code: {project_type_code}")
        return list(self._parameters.get(project_type_code, []))

    @property
    def recalculation_status(self) -> str:
        return "completed-server-side" if self._recalculation_command else "required-on-open"

    def infer_type_parameters(
        self,
        project_type_code: str,
        facts: Iterable[dict[str, Any]],
        explicit: Iterable[dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Conservatively map extracted numeric facts to this project type's slots.

        Explicit values always win. A fact is used only when its wording and unit
        identify one slot strongly enough; ambiguous INFO slots are addressed by
        slot number rather than their repeated influence code.
        """
        definitions = self.parameter_definitions(project_type_code)
        explicit_values = [dict(item) for item in explicit]
        occupied_slots: set[int] = set()
        for item in explicit_values:
            if item.get("slot_number") is not None:
                occupied_slots.add(int(item["slot_number"]))
            code = str(item.get("influence_code") or "").casefold()
            name = str(item.get("parameter_name") or "").casefold()
            for definition in definitions:
                if code and definition.influence_code.casefold() == code:
                    occupied_slots.add(definition.slot_number)
                if name and definition.parameter_name.casefold() == name:
                    occupied_slots.add(definition.slot_number)

        ranked_candidates: list[
            tuple[int, int, TypeParameterDefinition, int | float]
        ] = []
        for fact_index, raw_fact in enumerate(facts):
            fact_name = str(raw_fact.get("name") or "").strip()
            fact_value = str(raw_fact.get("value") or "").strip()
            matches = [
                match
                for match in _NUMBER_IN_TEXT_RE.finditer(fact_value)
                if not re.match(
                    r"\s*(?:-[а-яё]+|[сc]\b)",
                    fact_value[match.end() :],
                    re.IGNORECASE,
                )
            ]
            if not fact_name or not matches:
                continue
            # Commercial briefs often state a base, uplift and the accepted
            # result as an equation (for example ``350 + 10% = 385``).  The
            # right-hand result is the estimate driver, not the first operand.
            match = matches[-1] if "=" in fact_value else matches[0]
            number = float(match.group(1).replace(",", "."))
            if number.is_integer():
                number = int(number)
            for definition in definitions:
                if definition.slot_number in occupied_slots:
                    continue
                score = self._fact_parameter_score(fact_name, fact_value, definition)
                if score < 4:
                    continue
                value = self._coerce_fact_parameter_value(
                    definition, number, fact_name, fact_value
                )
                if value is None:
                    continue
                try:
                    self._validate_parameter_value(definition, value)
                except ExcelEstimateError:
                    continue
                ranked_candidates.append((score, fact_index, definition, value))

        # Facts are not ordered by relevance in model output.  Resolve the
        # strongest fact/slot pairs globally so an early incidental number
        # cannot occupy a slot before a later exact volume or duration fact.
        ranked_candidates.sort(key=lambda item: (-item[0], item[1], item[2].slot_number))
        inferred: list[dict[str, Any]] = []
        used_facts: set[int] = set()
        for _score, fact_index, definition, value in ranked_candidates:
            if fact_index in used_facts or definition.slot_number in occupied_slots:
                continue
            inferred.append({"slot_number": definition.slot_number, "value": value})
            used_facts.add(fact_index)
            occupied_slots.add(definition.slot_number)
        return [*explicit_values, *inferred]

    @staticmethod
    def _coerce_fact_parameter_value(
        definition: TypeParameterDefinition,
        number: int | float,
        fact_name: str,
        fact_value: str,
    ) -> int | float | None:
        """Accept a fact only when its unit is compatible with the Excel slot."""
        unit = (definition.unit or "").casefold().strip()
        haystack = f"{fact_name} {fact_value}".casefold()
        if unit == "%":
            if "%" not in fact_value and "процент" not in haystack:
                return None
            return number / 100 if number > 1 else number
        if "мес" in unit:
            if re.search(r"\b(мес|месяц|месяца|месяцев)\b", haystack):
                # A frequency such as "350 requests per month" is not a term.
                duration_markers = (
                    "срок",
                    "период",
                    "длительност",
                    "договор",
                    "обслуживан",
                    "подписк",
                    "лицензи",
                    "аренд",
                )
                return number if any(item in haystack for item in duration_markers) else None
            if re.search(r"\b(год|года|лет)\b", haystack):
                # Four-digit values are calendar years, never a duration.
                return number * 12 if 0 < number <= 20 else None
            return None
        if unit == "дн." or unit == "дн":
            if re.search(r"\b(дн|день|дня|дней)\b", haystack):
                return number
            if re.search(r"\b(нед|неделя|недели|недель)\b", haystack):
                return number * 7
            return None
        if unit == "ч":
            if re.search(r"\b(ч|час|часа|часов)\b", haystack):
                return number
            if re.search(r"\b(мин|минута|минуты|минут)\b", haystack):
                return number / 60
            return None
        if "ч/нед" in unit:
            if re.search(
                r"\b(ч|час|часа|часов)\b.{0,20}\b(нед|неделю|неделя|недели)\b",
                haystack,
            ):
                return number
            return None
        if unit == "fte" and "fte" not in haystack:
            return None
        return number

    @staticmethod
    def _fact_parameter_score(
        fact_name: str,
        fact_value: str,
        definition: TypeParameterDefinition,
    ) -> int:
        fact_tokens = set(_WORD_RE.findall(fact_name.casefold()))
        parameter_tokens = set(_WORD_RE.findall(definition.parameter_name.casefold()))
        common = fact_tokens & parameter_tokens
        score = len(common) * 3
        haystack = f"{fact_name} {fact_value}".casefold()
        aliases = {
            "QTY": {"количество", "объем", "объём", "пользователей", "систем", "лицензий", "единиц", "объектов"},
            "TERM_MONTHS": {"срок", "период", "длительность", "договор", "обслуживание", "подписка", "лицензия", "аренда"},
            "COMPLEXITY": {"сложность", "коэффициент", "коэф"},
            "PARALLEL_FTE": {"fte", "команда", "специалистов", "параллельных"},
            "RISK_PCT": {"риск", "резерв"},
            "LEAD_DAYS": {"поставка", "согласование", "запуск", "доступ", "дней", "дни"},
            "TARGET_MARGIN": {"маржа", "маржинальность"},
            "MARKUP": {"наценка"},
        }
        score += sum(2 for alias in aliases.get(definition.influence_code, set()) if alias in haystack)
        parameter_name = definition.parameter_name.casefold()
        info_aliases: set[str] = set()
        if any(marker in parameter_name for marker in ("обращен", "событ")):
            info_aliases = {"обращен", "событ", "заяв", "тикет", "инцидент"}
        elif "площад" in parameter_name or "организац" in parameter_name:
            info_aliases = {"площад", "офис", "филиал", "организац", "локац"}
        elif "интеграц" in parameter_name or "миграц" in parameter_name:
            info_aliases = {"интеграц", "миграц", "поток"}
        elif "сесс" in parameter_name:
            info_aliases = {"сесс", "воркшоп", "интервью"}
        elif "документ" in parameter_name:
            info_aliases = {"документ", "регламент", "инструкц"}
        score += sum(4 for alias in info_aliases if alias in haystack)
        unit = (definition.unit or "").casefold()
        if "мес" in unit and re.search(r"\b(мес|месяц|месяцев|месяца)\b", haystack):
            duration_markers = (
                "срок",
                "период",
                "длительност",
                "договор",
                "обслуживан",
                "подписк",
                "лицензи",
                "аренд",
            )
            # "Обращений в месяц" is a frequency, not a duration.  A month
            # unit reinforces TERM_MONTHS only together with duration wording.
            score += 4 if any(marker in haystack for marker in duration_markers) else -3
        if "дн" in unit and re.search(r"\b(дн|день|дней|дня)\b", haystack):
            score += 4
        if unit == "%" and ("%" in fact_value or "процент" in haystack):
            score += 3
        return score

    def build(self, payload: ExcelEstimateRequest) -> bytes:
        if payload.project_type_code not in self._project_type_codes:
            raise ExcelEstimateError(
                f"unknown project_type_code: {payload.project_type_code}"
            )
        rows = self._expand_work_items(payload.work_items)
        if len(rows) > 100:
            raise ExcelEstimateError(
                f"role assignments expand to {len(rows)} rows; the workbook limit is 100"
            )
        parameters = self._resolve_parameters(payload)
        self._validate_dependencies(payload, parameters)
        package = _WorkbookPackage(self._template)
        package.clear("Ввод", ["D20:D27", "A36:D39"])
        package.clear("Расчёт", ["A6:J105", "U6:V105"])
        package.clear("Внешние затраты", ["A6:G25", "K6:K25"])

        package.write_cells(
            "Ввод",
            {
                "B4": payload.project_name,
                "B5": payload.project_type_code,
                "B6": payload.estimate_date,
                "B7": payload.estimate_mode,
                "B8": payload.confidence,
                "B9": payload.vat_rate,
                "B10": payload.discount_rate,
                "B11": payload.work_hours_per_day,
                "B12": payload.planned_start_date,
                "B13": payload.default_hours_basis,
                "B14": payload.source_or_spec_version,
                "B15": payload.main_assumption,
                "B16": payload.commercial_reserve_rate,
                **{f"D{19 + slot}": value for slot, value in parameters.items()},
            },
        )
        for index, item in enumerate(payload.assumptions):
            row = 36 + index
            package.write_cells(
                "Ввод",
                {
                    f"A{row}": item.type,
                    f"B{row}": item.text,
                    f"C{row}": item.source,
                    f"D{row}": item.impact,
                },
            )

        for index, item in enumerate(rows):
            row = 6 + index
            package.write_cells(
                "Расчёт",
                {
                    f"A{row}": item.stage_no,
                    f"B{row}": item.stage_name,
                    f"C{row}": item.work_no,
                    f"D{row}": item.work_name,
                    f"E{row}": item.role,
                    f"F{row}": item.estimated_hours,
                    f"G{row}": item.hours_basis,
                    f"H{row}": item.quantity_override,
                    f"I{row}": item.sale_rate_override,
                    f"J{row}": item.cost_rate_override,
                    f"U{row}": item.site_or_contour,
                    f"V{row}": item.comment,
                },
            )

        for index, item in enumerate(payload.external_costs):
            row = 6 + index
            package.write_cells(
                "Внешние затраты",
                {
                    f"A{row}": item.category,
                    f"B{row}": item.description,
                    f"C{row}": item.quantity,
                    f"D{row}": item.unit,
                    f"E{row}": item.unit_cost,
                    f"F{row}": item.unit_sale_price_override,
                    f"G{row}": item.periodicity,
                    f"K{row}": item.comment,
                },
            )

        package.force_full_recalculation()
        if package.formula_fingerprint() != self._template_formula_fingerprint:
            raise ExcelEstimateError("formula integrity check failed")
        result = package.to_bytes()
        if self._recalculation_command:
            result = self._recalculate_with_libreoffice(result)
        return result

    @staticmethod
    def _validate_template_compatibility(package: _WorkbookPackage) -> None:
        expected = (
            "IF(ISNUMBER(MATCH('Ввод'!$B$5,"
            "'Справочник типов'!$A$2:$A$27,0)),1,0)"
        )
        actual = next(
            (
                formula
                for sheet, cell, formula in package.formula_fingerprint()
                if sheet == "Проверки" and cell == "B6"
            ),
            None,
        )
        if actual != expected:
            raise ExcelEstimateError(
                "Excel template is incompatible: Проверки!B6 must use the "
                "cross-engine MATCH formula"
            )

    def _recalculate_with_libreoffice(self, workbook: bytes) -> bytes:
        """Open/save in a real spreadsheet engine so formula caches are current."""
        with tempfile.TemporaryDirectory(prefix="projectile-xlsx-") as directory:
            root = Path(directory)
            source_dir = root / "source"
            output_dir = root / "output"
            profile_dir = root / "profile"
            source_dir.mkdir()
            output_dir.mkdir()
            profile_dir.mkdir()
            source = source_dir / "estimate.xlsx"
            source.write_bytes(workbook)
            try:
                completed = subprocess.run(
                    [
                        self._recalculation_command,
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nofirststartwizard",
                        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                        "--convert-to",
                        "xlsx",
                        "--outdir",
                        str(output_dir),
                        str(source),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._recalculation_timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ExcelEstimateError(
                    f"workbook recalculation engine failed: {error}"
                ) from error
            target = output_dir / source.name
            if completed.returncode != 0 or not target.is_file():
                details = (completed.stderr or completed.stdout).strip()[-1000:]
                raise ExcelEstimateError(
                    "workbook recalculation engine did not produce an xlsx"
                    + (f": {details}" if details else "")
                )
            recalculated = target.read_bytes()
            try:
                verification = _WorkbookPackage(recalculated)
            except (ValueError, KeyError, zipfile.BadZipFile) as error:
                raise ExcelEstimateError(
                    "recalculation engine returned an invalid workbook"
                ) from error
            missing = self.REQUIRED_SHEETS - set(verification.sheet_paths)
            if missing or not verification.formula_fingerprint():
                raise ExcelEstimateError(
                    "recalculation engine damaged workbook structure or formulas"
                )
            self._refresh_compatibility_status_caches(verification)
            return verification.to_bytes()

    @staticmethod
    def _refresh_compatibility_status_caches(package: _WorkbookPackage) -> None:
        """Refresh status cells that LibreOffice occasionally leaves one step stale."""
        recognized = package.read_rows("Проверки", 6, 6, 2, 2)[0][0] == 1
        package.write_formula_cached_value(
            "Проверки", "E6", "PASS" if recognized else "FAIL"
        )
        statuses = [
            row[0] for row in package.read_rows("Проверки", 7, 14, 5, 5)
        ]
        model_status = "FAIL" if not recognized or "FAIL" in statuses else "PASS"
        package.write_formula_cached_value("Проверки", "B3", model_status)
        package.write_formula_cached_value("Итого по проекту", "B11", model_status)

    def _resolve_parameters(self, payload: ExcelEstimateRequest) -> dict[int, Any]:
        definitions = self.parameter_definitions(payload.project_type_code)
        by_slot = {item.slot_number: item for item in definitions}
        resolved: dict[int, Any] = {}
        for supplied in payload.type_parameters:
            matches = list(definitions)
            if supplied.slot_number is not None:
                matches = [
                    item for item in matches if item.slot_number == supplied.slot_number
                ]
            if supplied.influence_code is not None:
                wanted = supplied.influence_code.strip().casefold()
                matches = [
                    item for item in matches if item.influence_code.casefold() == wanted
                ]
            if supplied.parameter_name is not None:
                wanted = supplied.parameter_name.strip().casefold()
                matches = [
                    item for item in matches if item.parameter_name.casefold() == wanted
                ]
            if not matches:
                raise ExcelEstimateError(
                    f"type parameter selector does not match {payload.project_type_code}"
                )
            if len(matches) > 1:
                raise ExcelEstimateError(
                    f"ambiguous parameter selector {supplied.influence_code!r}; "
                    "add slot_number or parameter_name"
                )
            definition = matches[0]
            if definition.slot_number in resolved:
                raise ExcelEstimateError(
                    f"type parameter slot {definition.slot_number} was supplied more than once"
                )
            self._validate_parameter_value(definition, supplied.value)
            resolved[definition.slot_number] = supplied.value

        for slot, definition in by_slot.items():
            if not (
                definition.required
                and slot not in resolved
                and definition.default_value in (None, "")
            ):
                continue
            external_pricing_needed = any(
                item.unit_sale_price_override is None for item in payload.external_costs
            )
            if (
                definition.influence_code in {"TARGET_MARGIN", "MARKUP"}
                and not external_pricing_needed
            ):
                # This neutral value satisfies the workbook's unconditional
                # required-field check without inventing a commercial margin.
                resolved[slot] = 0
                continue
            raise ExcelEstimateError(
                f"required type parameter has no value or default: {definition.parameter_name}"
            )
        return resolved

    def _validate_dependencies(
        self, payload: ExcelEstimateRequest, supplied: dict[int, Any]
    ) -> None:
        definitions = self.parameter_definitions(payload.project_type_code)
        effective = {
            definition.influence_code: supplied.get(
                definition.slot_number, definition.default_value
            )
            for definition in definitions
        }
        project_default = self._default_hours_basis.get(
            payload.project_type_code, "Всего"
        )

        def resolved_basis(item: ExcelWorkItem) -> str:
            if item.hours_basis != "Авто":
                return item.hours_basis
            if payload.default_hours_basis != "Авто":
                return payload.default_hours_basis
            return project_default

        monthly_needed = any(
            resolved_basis(item) in {"В месяц", "Ед. × месяц"}
            for item in payload.work_items
        ) or any(item.periodicity == "В месяц" for item in payload.external_costs)
        if monthly_needed and not _positive_number(effective.get("TERM_MONTHS")):
            raise ExcelEstimateError(
                "monthly works or costs require a positive TERM_MONTHS parameter"
            )

        for item in payload.work_items:
            if resolved_basis(item) not in {"На единицу", "Ед. × месяц"}:
                continue
            if item.quantity_override is None and not _positive_number(
                effective.get("QTY")
            ):
                raise ExcelEstimateError(
                    f"work {item.work_no} requires quantity_override or a positive QTY parameter"
                )

    @staticmethod
    def _validate_parameter_value(
        definition: TypeParameterDefinition, value: str | float
    ) -> None:
        code = definition.influence_code
        if code == "INFO":
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExcelEstimateError(f"{definition.parameter_name} must be numeric")
        if not math.isfinite(float(value)):
            raise ExcelEstimateError(f"{definition.parameter_name} must be finite")
        if code in {"RISK_PCT", "MARKUP", "TARGET_MARGIN"} and not 0 <= value <= 1:
            raise ExcelEstimateError(
                f"{definition.parameter_name} must be a decimal fraction from 0 to 1"
            )
        if code in {"QTY", "TERM_MONTHS", "COMPLEXITY", "PARALLEL_FTE"} and value <= 0:
            raise ExcelEstimateError(
                f"{definition.parameter_name} must be greater than zero"
            )
        if code == "LEAD_DAYS" and value < 0:
            raise ExcelEstimateError(f"{definition.parameter_name} cannot be negative")

    def _expand_work_items(
        self, items: Iterable[ExcelWorkItem]
    ) -> list[_ExpandedWorkRow]:
        result: list[_ExpandedWorkRow] = []
        for item in items:
            assignments = item.role_assignments or [
                ExcelRoleAssignment(
                    role=item.role or "",
                    estimated_hours=item.estimated_hours or 0,
                    sale_rate_override=item.sale_rate_override,
                    cost_rate_override=item.cost_rate_override,
                )
            ]
            for assignment in assignments:
                comment_parts = [item.comment, assignment.responsibility]
                result.append(
                    _ExpandedWorkRow(
                        stage_no=item.stage_no,
                        stage_name=item.stage_name.strip(),
                        work_no=item.work_no,
                        work_name=item.work_name.strip(),
                        role=self._normalize_role(assignment.role),
                        estimated_hours=assignment.estimated_hours,
                        hours_basis=item.hours_basis,
                        quantity_override=item.quantity_override,
                        sale_rate_override=assignment.sale_rate_override,
                        cost_rate_override=assignment.cost_rate_override,
                        site_or_contour=item.site_or_contour,
                        comment="; ".join(
                            part.strip() for part in comment_parts if part
                        ),
                    )
                )
        return result

    def _normalize_role(self, supplied: str) -> str:
        value = supplied.strip()
        external_code = self._internal_role_codes.get(value, value)
        code_match = re.search(r"#\s*(\d+)", external_code)
        code = (
            code_match.group(1)
            if code_match
            else external_code
            if external_code.isdigit()
            else None
        )
        for role in self._roles:
            if value.casefold() == role.exact_text.casefold():
                return role.exact_text
            if code == role.external_code:
                return role.exact_text
        raise ExcelEstimateError(f"unknown role: {supplied}")


class _WorkbookPackage:
    def __init__(self, source: bytes) -> None:
        self._entries: list[tuple[zipfile.ZipInfo, bytes]] = []
        with zipfile.ZipFile(BytesIO(source), "r") as archive:
            for info in archive.infolist():
                self._entries.append((info, archive.read(info.filename)))
        self._entry_map = {info.filename: data for info, data in self._entries}
        self._namespace_declarations = {
            name: _namespace_declarations(data)
            for name, data in self._entry_map.items()
            if name.endswith(".xml")
        }
        self._register_namespaces()
        self.sheet_paths = self._sheet_paths()
        self._shared_strings = self._read_shared_strings()
        self._trees: dict[str, ET.Element] = {}

    def _register_namespaces(self) -> None:
        seen: set[tuple[str, str]] = set()
        for name in ("xl/workbook.xml", *self._worksheet_entry_names()):
            data = self._entry_map.get(name)
            if data is None:
                continue
            for _, namespace in ET.iterparse(BytesIO(data), events=("start-ns",)):
                prefix, uri = namespace
                if (prefix, uri) in seen or re.fullmatch(r"ns\d+", prefix or ""):
                    continue
                seen.add((prefix, uri))
                ET.register_namespace(prefix or "", uri)

    def _worksheet_entry_names(self) -> list[str]:
        return [name for name in self._entry_map if name.startswith("xl/worksheets/")]

    def _sheet_paths(self) -> dict[str, str]:
        workbook = ET.fromstring(self._entry_map["xl/workbook.xml"])
        rels = ET.fromstring(self._entry_map["xl/_rels/workbook.xml.rels"])
        relationships = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in rels.findall(f"{{{_PKG_REL_NS}}}Relationship")
        }
        result = {}
        for sheet in workbook.findall(f".//{{{_MAIN_NS}}}sheet"):
            target = relationships[sheet.attrib[f"{{{_REL_NS}}}id"]]
            result[sheet.attrib["name"]] = posixpath.normpath(
                posixpath.join("xl", target)
            )
        return result

    def _read_shared_strings(self) -> list[str]:
        data = self._entry_map.get("xl/sharedStrings.xml")
        if data is None:
            return []
        root = ET.fromstring(data)
        return [
            "".join(node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t"))
            for item in root.findall(f"{{{_MAIN_NS}}}si")
        ]

    def _tree(self, entry_name: str) -> ET.Element:
        if entry_name not in self._trees:
            self._trees[entry_name] = ET.fromstring(self._entry_map[entry_name])
        return self._trees[entry_name]

    def read_rows(
        self,
        sheet_name: str,
        first_row: int,
        last_row: int,
        first_col: int,
        last_col: int,
    ) -> list[list[Any]]:
        root = self._tree(self.sheet_paths[sheet_name])
        cells = {
            cell.attrib["r"]: self._cell_value(cell)
            for cell in root.findall(f".//{{{_MAIN_NS}}}c")
            if "r" in cell.attrib
        }
        return [
            [
                cells.get(f"{_column_name(col)}{row}")
                for col in range(first_col, last_col + 1)
            ]
            for row in range(first_row, last_row + 1)
        ]

    def _cell_value(self, cell: ET.Element) -> Any:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.iter(f"{{{_MAIN_NS}}}t"))
        value = cell.find(f"{{{_MAIN_NS}}}v")
        if value is None or value.text is None:
            return None
        if cell_type == "s":
            return self._shared_strings[int(value.text)]
        if cell_type == "b":
            return value.text == "1"
        if cell_type in {"str", "e"}:
            return value.text
        try:
            number = float(value.text)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value.text

    def clear(self, sheet_name: str, ranges: Iterable[str]) -> None:
        for cell_range in ranges:
            start, end = cell_range.split(":")
            start_col, start_row = _split_cell(start)
            end_col, end_row = _split_cell(end)
            for row in range(start_row, end_row + 1):
                self.write_cells(
                    sheet_name,
                    {
                        f"{_column_name(col)}{row}": None
                        for col in range(start_col, end_col + 1)
                    },
                )

    def write_cells(self, sheet_name: str, values: dict[str, Any]) -> None:
        root = self._tree(self.sheet_paths[sheet_name])
        sheet_data = root.find(f"{{{_MAIN_NS}}}sheetData")
        if sheet_data is None:
            raise ExcelEstimateError(f"sheet {sheet_name} has no sheetData")
        rows = {
            int(row.attrib["r"]): row
            for row in sheet_data.findall(f"{{{_MAIN_NS}}}row")
        }
        for address, value in values.items():
            _, row_number = _split_cell(address)
            row = rows.get(row_number)
            if row is None:
                row = ET.Element(f"{{{_MAIN_NS}}}row", {"r": str(row_number)})
                sheet_data.append(row)
                rows[row_number] = row
            cell = next(
                (
                    item
                    for item in row.findall(f"{{{_MAIN_NS}}}c")
                    if item.attrib.get("r") == address
                ),
                None,
            )
            if cell is None:
                cell = ET.Element(f"{{{_MAIN_NS}}}c", {"r": address})
                row.append(cell)
            self._set_cell_value(cell, value)

    def write_formula_cached_value(
        self, sheet_name: str, address: str, value: str | int | float
    ) -> None:
        root = self._tree(self.sheet_paths[sheet_name])
        cell = next(
            (
                item
                for item in root.findall(f".//{{{_MAIN_NS}}}c")
                if item.attrib.get("r") == address
            ),
            None,
        )
        if cell is None or cell.find(f"{{{_MAIN_NS}}}f") is None:
            raise ExcelEstimateError(f"formula target does not exist: {sheet_name}!{address}")
        for child in list(cell):
            if child.tag in {f"{{{_MAIN_NS}}}v", f"{{{_MAIN_NS}}}is"}:
                cell.remove(child)
        if isinstance(value, str):
            cell.attrib["t"] = "str"
        else:
            cell.attrib.pop("t", None)
        ET.SubElement(cell, f"{{{_MAIN_NS}}}v").text = str(value)

    @staticmethod
    def _set_cell_value(cell: ET.Element, value: Any) -> None:
        for child in list(cell):
            if child.tag in {
                f"{{{_MAIN_NS}}}f",
                f"{{{_MAIN_NS}}}v",
                f"{{{_MAIN_NS}}}is",
            }:
                cell.remove(child)
        cell.attrib.pop("t", None)
        if value is None:
            return
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            value = (value - date(1899, 12, 30)).days
        if isinstance(value, bool):
            cell.attrib["t"] = "b"
            ET.SubElement(cell, f"{{{_MAIN_NS}}}v").text = "1" if value else "0"
            return
        if isinstance(value, (int, float)):
            if not math.isfinite(float(value)):
                raise ExcelEstimateError("Excel numeric values must be finite")
            ET.SubElement(cell, f"{{{_MAIN_NS}}}v").text = format(value, ".15g")
            return
        cell.attrib["t"] = "inlineStr"
        inline = ET.SubElement(cell, f"{{{_MAIN_NS}}}is")
        text = ET.SubElement(inline, f"{{{_MAIN_NS}}}t")
        rendered = str(value)
        if rendered != rendered.strip():
            text.attrib[f"{{{_XML_NS}}}space"] = "preserve"
        text.text = rendered

    def force_full_recalculation(self) -> None:
        root = self._tree("xl/workbook.xml")
        calc = root.find(f"{{{_MAIN_NS}}}calcPr")
        if calc is None:
            calc = ET.SubElement(root, f"{{{_MAIN_NS}}}calcPr")
        calc.attrib.update(
            {
                "calcId": "0",
                "calcMode": "auto",
                "fullCalcOnLoad": "1",
                "forceFullCalc": "1",
            }
        )

    def formula_fingerprint(self) -> tuple[tuple[str, str, str], ...]:
        formulas = []
        for sheet_name, entry_name in self.sheet_paths.items():
            root = self._tree(entry_name)
            for cell in root.findall(f".//{{{_MAIN_NS}}}c"):
                formula = cell.find(f"{{{_MAIN_NS}}}f")
                if formula is not None:
                    formulas.append(
                        (sheet_name, cell.attrib.get("r", ""), formula.text or "")
                    )
        return tuple(formulas)

    def to_bytes(self) -> bytes:
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for info, original in self._entries:
                data = original
                if info.filename in self._trees:
                    # Excel uses namespace prefixes in mc:Ignorable and
                    # mc:Choice/@Requires even when no element has that prefix.
                    # ElementTree otherwise drops those declarations.
                    ET.register_namespace("", _MAIN_NS)
                    data = ET.tostring(
                        self._trees[info.filename],
                        encoding="utf-8",
                        xml_declaration=True,
                    )
                    data = _restore_namespace_declarations(
                        data, self._namespace_declarations.get(info.filename, {})
                    )
                archive.writestr(info, data)
        return output.getvalue()


def _split_cell(address: str) -> tuple[int, int]:
    match = _CELL_RE.fullmatch(address)
    if match is None:
        raise ExcelEstimateError(f"invalid cell address: {address}")
    col = 0
    for character in match.group(1):
        col = col * 26 + ord(character) - 64
    return col, int(match.group(2))


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _positive_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value > 0
    )


def _namespace_declarations(xml: bytes) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for _, namespace in ET.iterparse(BytesIO(xml), events=("start-ns",)):
        prefix, uri = namespace
        declarations.setdefault(prefix or "", uri)
    return declarations


def _restore_namespace_declarations(xml: bytes, expected: dict[str, str]) -> bytes:
    declaration_end = xml.find(b"?>")
    start_begin = xml.find(b"<", declaration_end + 2 if declaration_end >= 0 else 0)
    start_end = xml.find(b">", start_begin)
    if start_begin < 0 or start_end < 0:
        return xml
    prefix_bytes = xml[:start_begin]
    start = xml[start_begin:start_end]
    for prefix, uri in expected.items():
        marker = f"xmlns{':' + prefix if prefix else ''}=".encode()
        if marker in start:
            continue
        declaration = f' xmlns{":" + prefix if prefix else ""}="{uri}"'.encode()
        start += declaration
    return prefix_bytes + start + xml[start_end:]
