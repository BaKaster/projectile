from app.analysis_contracts import ExtractedFact, ModelAnalysis
from app.analysis_worker import _uses_managed_service_catalog
from app.analyzer import _add_derived_capacity_facts, _apply_classification_guardrails


ALLOWED = {
    "SEC_Implementation",
    "SUP_IT_Implementation",
    "SUP_App_Support",
    "SUP_Cloud_PaaS",
    "SUP_Complex",
    "SUP_L3_SW",
}


def _analysis(code: str, summary: str) -> ModelAnalysis:
    return ModelAnalysis(
        project_type_code=code,
        confidence="high",
        summary=summary,
        rationale=summary,
    )


def test_non_security_implementation_cannot_be_classified_as_sec() -> None:
    result = _analysis("SEC_Implementation", "Внедрение CRM и настройка отчётности")
    _apply_classification_guardrails(result, ALLOWED)
    assert result.project_type_code == "SUP_IT_Implementation"


def test_new_functionality_is_not_application_support() -> None:
    result = _analysis("SUP_App_Support", "Создание нового отчёта и разработка интеграции")
    _apply_classification_guardrails(result, ALLOWED)
    assert result.project_type_code == "SUP_IT_Implementation"


def test_existing_service_with_integration_uses_mixed_service_catalog() -> None:
    assert _uses_managed_service_catalog(
        "existing_solution", "integration", "SUP_IT_Implementation"
    )
    assert _uses_managed_service_catalog(
        "mixed", "mixed", "SUP_IT_Implementation"
    )
    assert not _uses_managed_service_catalog(
        "new_solution", "implementation", "SUP_IT_Implementation"
    )


def test_separate_supported_service_facts_produce_numeric_capacity_driver() -> None:
    result = _analysis("SUP_App_Support", "Поддержка двух существующих сервисов")
    result.facts = [
        ExtractedFact(
            name="Состав сервиса терминального доступа",
            value="Эксплуатация и решение инцидентов.",
            source_document_ids=["terminal"],
        ),
        ExtractedFact(
            name="Сервис 1С входит в поддержку",
            value="Эксплуатация и решение инцидентов.",
            source_document_ids=["1c"],
        ),
    ]

    _add_derived_capacity_facts(result)

    capacity = result.facts[-1]
    assert capacity.name == "Объём поддерживаемых сервисов"
    assert capacity.value == "2 сервиса"
    assert capacity.source_document_ids == ["1c", "terminal"]


def test_cloud_is_only_a_placement_attribute_for_new_office() -> None:
    result = _analysis(
        "SUP_Cloud_PaaS",
        "Открытие нового офиса: настройка AD и VPN с размещением в облаке",
    )
    _apply_classification_guardrails(result, ALLOWED)
    assert result.project_type_code == "SUP_IT_Implementation"


def test_explicit_security_scope_keeps_sec_direction() -> None:
    result = _analysis("SEC_Implementation", "Внедрение EDR и средств защиты информации")
    _apply_classification_guardrails(result, ALLOWED)
    assert result.project_type_code == "SEC_Implementation"


def test_monitoring_service_is_not_a_complex_multistream_project() -> None:
    result = _analysis(
        "SUP_Complex",
        "Техническая поддержка технологического сервиса управления мониторингом",
    )
    _apply_classification_guardrails(result, ALLOWED)
    assert result.project_type_code == "SUP_L3_SW"

    result = _analysis(
        "SEC_Implementation",
        "Техническая поддержка технологического сервиса управления мониторингом",
    )
    _apply_classification_guardrails(result, ALLOWED)
    assert result.project_type_code == "SUP_L3_SW"
