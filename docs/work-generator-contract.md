# Контракт генератора работ по этапам

Готовые модели находятся в [`app/work_contracts.py`](../app/work_contracts.py).

## Ответственность

`StagePlanner` определяет допустимые этапы, порядок, применимость, gate и правила
декомпозиции. Генератор определяет конкретные операции, роли, нормативы, количественные
драйверы, часы, источники и допущения.

Генератор не должен переименовывать `stage_code`, создавать неизвестный этап, помещать
работы в этап-кандидат или менять exit gate.

## Рекомендуемый интерфейс

```python
from collections.abc import Mapping
from typing import Protocol

from app.stage_contracts import ProjectStagePlan, ResolvedStage
from app.work_contracts import GeneratedWorkPlan, StageWorkPackage


class StageWorkGenerator(Protocol):
    def generate_stage(
        self, stage: ResolvedStage, facts: Mapping[str, object]
    ) -> StageWorkPackage: ...


def generate_work_plan(generator, stage_plan, facts):
    packages = [
        generator.generate_stage(stage, facts)
        for stage in stage_plan.stages
        if stage.status == "selected"
    ]
    result = GeneratedWorkPlan(
        project_type_code=stage_plan.project_type_code,
        stage_schema_version=stage_plan.schema_version,
        packages=packages,
    )
    result.validate_against(stage_plan)
    return result
```

## Поля этапа

- `objective` — зачем существует этап;
- `entry_criteria` — что проверить до генерации;
- `deliverables` — что должны совместно создать работы;
- `exit_gate.acceptance_criteria` — какие проверки покрыть;
- `work_generation.required_inputs` — какие факты запросить или допустить;
- `work_categories` — допустимые группы работ;
- `decomposition_rule` — единица разбиения;
- `estimation_drivers` — параметры нормативной/параметрической оценки.

## Алгоритм

1. Получить `ProjectStagePlan` только из resolver/API.
2. Передать генератору только `selected`.
3. Найти подтверждённый факт для каждого `required_input`. Иначе записать безопасное
   допущение или вернуть материальный вопрос.
4. Создать работы по `decomposition_rule`, а не одним общим пунктом.
5. Выбрать `norm`, `parametric`, `analogy` или `expert`.
6. Для существенных работ сохранить `source_document_ids`.
7. Вызвать `validate_against` до сохранения или экспорта.

```json
{
  "project_type_code": "SUP_IT_Implementation",
  "stage_schema_version": "1.0.0",
  "packages": [
    {
      "stage_code": "cutover_migration",
      "works": [
        {
          "work_code": "migration.wave.execute",
          "name": "Миграция волны почтовых ящиков",
          "description": "Перенос, сверка и стабилизация одной согласованной волны.",
          "role_code": "mail_engineer",
          "estimate_method": "parametric",
          "effort_hours": 32,
          "source_document_ids": ["document-id"],
          "assumptions": ["В волне 250 ящиков, допустимое окно 8 часов"]
        }
      ]
    }
  ]
}
```

`work_code` должен быть стабилен для статистики аналогов; описание можно адаптировать.

