from types import SimpleNamespace

from app.rates import parse_rate_text, project_input_error, split_rate_update_text


def test_exact_role_rate_row_is_recognised_and_removed_from_project_text() -> None:
    text = "L3 DevOps-инженер #1202: продажа 4 500, себестоимость 2 900 рублей в час"
    roles = [
        SimpleNamespace(
            code="devops_l3", name="L3 DevOps-инженер", external_id=1202
        )
    ]

    items = parse_rate_text(text, roles)

    assert items[0]["sale_rate"] == 4500
    assert items[0]["cost_rate"] == 2900
    assert split_rate_update_text(text, items) == ""


def test_obvious_garbage_is_not_a_project_description() -> None:
    assert project_input_error("тест") is not None
    assert project_input_error("Организовать поддержку ERP-системы для трёх филиалов.") is None
