# Projectile

Инструкция и готовый промпт для передачи frontend-разработчику: [docs/frontend-handoff.md](docs/frontend-handoff.md).

Backend для загрузки проектных документов, их последующего анализа и формирования предварительной оценки проекта.

Готовый интерфейс находится в [`frontend/`](frontend/README.md). Для локального запуска
без установки зависимостей выполните `python -m http.server 5173 --directory frontend`
из корня репозитория и откройте <http://localhost:5173>.

## Что реализовано

- FastAPI и интерактивная документация OpenAPI;
- создание проекта;
- загрузка одного или нескольких файлов любого формата;
- потоковое чтение файлов без загрузки всего содержимого в память приложения;
- SHA-256, дедупликация неизменённых файлов и версии одноимённых документов;
- идемпотентность запросов через `Idempotency-Key`;
- PostgreSQL для проектов, метаданных документов, связей и запусков обработки;
- локальное файловое хранилище для бинарников;
- автоматическая загрузка 26 корпоративных типов проектов в `project_types`;
- определение этапов по 26 профилям с gates и сигналами применимости;
- адаптивное формирование работ по выбранным этапам, фактам и сигналам проекта.

Бинарное содержимое намеренно не кладётся в PostgreSQL. В БД сохраняются метаданные и `storage_uri`, а сам файл находится в `storage/`, как предусмотрено архитектурой.

## Этапы проектов

Справочник различает аудит, консалтинг, внедрение/миграцию, поддержку, облачный сервис,
поставку, аренду и комплексный проект. Условные этапы активируются подтверждёнными
сигналами, а при нехватке сведений остаются кандидатами для проверки.

```powershell
python -m app.stages_cli validate
python -m app.stages_cli plan SUP_IT_Implementation --signal migration --signal pilot
```

Production-каталог работ находится в `data/project-work-templates.json`. Он содержит уже
извлечённые правила и во время работы не обращается к архиву проектов или внешним источникам.
Обязательные работы наследуются от шаблона этапа, условные активируются подтверждёнными
сигналами, а специализация типа проекта добавляет предметные операции. При анализе документов
модель получает этот каталог как ориентир и отдельно извлекает явно требуемые уникальные
работы проекта, которых нет в типовом составе. Поэтому JSON не является жёстким закрытым
списком и не требует доступа к архиву примеров на production.

Методика: [docs/project-stage-methodology.md](docs/project-stage-methodology.md).
Контракт Python-генератора работ: [docs/work-generator-contract.md](docs/work-generator-contract.md).

## Запуск через Docker

```powershell
docker compose up -d --build
```

После запуска:

- API: `http://localhost:8000`;
- Swagger UI: `http://localhost:8000/docs`;
- health check: `http://localhost:8000/health`;
- PostgreSQL с хоста: `localhost:55432`;
- имя базы, пользователь и пароль берутся из `.env`; пароль по умолчанию отсутствует.

Порт `55432` выбран для PostgreSQL, чтобы не конфликтовать с локальным PostgreSQL на стандартном порту `5432`. Его можно изменить через `POSTGRES_PORT`.

Остановить сервисы:

```powershell
docker compose down
```

Данные PostgreSQL сохраняются в Docker volume `projectile_postgres_data`. Команда `docker compose down` их не удаляет.

## API

### Excel-расчёт стоимости

Production-книга формируется как копия утверждённого шаблона: сервис очищает и
заполняет только входные диапазоны из `Карта_входных_ячеек_MONSters_v2.txt`, сохраняет
формулы и, если настроен `PROJECTILE_EXCEL_RECALCULATION_COMMAND`, пересчитывает и
сохраняет книгу серверным табличным движком до возврата клиенту.

Основной пользовательский сценарий уже подключён к кнопке «Сформировать Excel файл»:

```http
GET /api/v1/projects/{projectId}/analysis-runs/{runId}/report.xlsx
```

Endpoint берёт сохранённые `stage_plan` и `work_plan` нейросети для указанного запуска,
разворачивает назначения ролей в строки расчёта, переносит существенные риски и допущения
и возвращает заполненную копию единого шаблона. Каждый запрос начинает работу с чистого
шаблона, поэтому данные разных проектов и повторных анализов не смешиваются.

Схему восьми параметров конкретного типа можно получить без жёсткой привязки их смысла
к номеру слота:

```http
GET /api/v1/project-types/SUP_IT_Implementation/estimate-parameters
```

Для ручного или экспертного сценария готовую книгу также можно сформировать явным payload:

```http
POST /api/v1/estimates/workbook
Content-Type: application/json

{
  "project_name": "Миграция платформы",
  "project_type_code": "SUP_IT_Implementation",
  "type_parameters": [
    {"influence_code": "QTY", "value": 12},
    {"influence_code": "RISK_PCT", "value": 0.1}
  ],
  "work_items": [
    {
      "stage_no": 1,
      "stage_name": "Проектирование",
      "work_no": "1.1",
      "work_name": "Спроектировать целевую архитектуру",
      "hours_basis": "Всего",
      "role_assignments": [
        {"role": "technical_architect", "estimated_hours": 24},
        {"role": "windows_l3", "estimated_hours": 8}
      ]
    }
  ],
  "external_costs": [],
  "assumptions": []
}
```

Поле `role` принимает внутренний код роли из `data/role-effort-catalog.json`, внешний
номер (`1202` или `#1202`) либо точную строку справочника Excel. Несколько назначений
одной работы разворачиваются в отдельные строки, поскольку одна строка шаблона содержит
одну роль. Лимиты проверяются после разворачивания: 100 строк работ, 20 внешних затрат и
4 проектных допущения. Проценты передаются десятичной долей.

Docker-конфигурация использует LibreOffice для обновления кэшированных результатов формул.
Дополнительно книга содержит флаги `fullCalcOnLoad`/`forceFullCalc`, поэтому Microsoft
Excel выполнит собственный полный пересчёт при открытии. Если серверный движок отключён,
API возвращает заголовок `X-Excel-Recalculation: required-on-open`.

### Формирование работ

```http
POST /api/v1/project-types/SUP_IT_Implementation/work-plan
Content-Type: application/json

{
  "stage_context": {
    "signals": ["migration"],
    "include_candidates": false
  },
  "work_context": {
    "signals": ["migration", "integration", "data_migration"],
    "facts": [
      {
        "name": "Объём переносимых данных",
        "value": "2 ТБ в трёх волнах",
        "source_document_ids": ["document-id"]
      }
    ],
    "project_specific_works": [
      {
        "stage_code": "solution_design",
        "name": "Разработать адаптер для проприетарной шины заказчика",
        "rationale": "Явное требование ТЗ вне типового состава",
        "outputs": ["Спецификация и реализованный адаптер"],
        "estimation_drivers": ["Количество типов сообщений"],
        "source_document_ids": ["document-id"]
      }
    ]
  }
}
```

Ответ содержит пакеты работ только для выбранных этапов, причину включения каждой работы,
релевантные факты проекта, проверяемые результаты и драйверы будущей оценки. Часы и роли на
этом шаге не выдумываются: они остаются незаполненными до подтверждения объёмов.

### Чатовый сценарий

Frontend использует проект как чат-сессию:

- `POST /api/v1/chats` — новый чат;
- `GET /api/v1/chats` — история;
- `GET /api/v1/chats/{chatId}` — сообщения и последний анализ;
- `POST /api/v1/chats/{chatId}/messages` — сообщение, создающее новый запуск анализа;
- `POST /api/v1/projects/{projectId}/analysis-runs/{runId}/answers` — ответ на вопросы;
- `POST /api/v1/projects/{projectId}/analysis-runs/{runId}/questions/skip` — пропуск вопросов;
- `GET /api/v1/projects/{projectId}/analysis-runs/{runId}/report.pdf` — PDF-отчёт.

Текст сообщения сохраняется как источник проекта и анализируется тем же pipeline, что и
загруженные документы. Идентификатор чата совпадает с `projectId`.

### Создание проекта

```http
POST /api/v1/projects
Content-Type: application/json

{
  "name": "Расчёт для заказчика"
}
```

При необходимости frontend может передать свой UUID в поле `id`.

### Загрузка документов

```http
POST /api/v1/projects/{projectId}/documents
Content-Type: multipart/form-data
Idempotency-Key: upload-unique-key
```

Каждый файл передаётся в повторяемом multipart-поле `files`. Пример для frontend:

```javascript
const body = new FormData();

for (const file of files) {
  body.append("files", file);
  body.append("relative_paths", file.webkitRelativePath || file.name);
}

const response = await fetch(
  `/api/v1/projects/${projectId}/documents`,
  {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body,
  },
);
```

Не нужно устанавливать `Content-Type` вручную: браузер сам добавит корректную multipart boundary.
Поле `relative_paths` необязательно, но его следует передавать при загрузке папки: оно
сохраняет структуру проекта и различает одноимённые файлы из разных подпапок.

Успешный ответ имеет статус `202 Accepted`:

```json
{
  "project_id": "c9c8908d-e995-4676-8ca7-9c53cd9fc457",
  "upload_run_id": "fb087390-f84e-42bc-ab48-48704a3b543d",
  "status": "uploaded",
  "documents": [
    {
      "id": "ea513dfd-cb92-46a5-b308-a247897aa50d",
      "original_filename": "ТЗ.pdf",
      "source_path": "requirements/ТЗ.pdf",
      "media_type": "application/pdf",
      "size_bytes": 123456,
      "sha256": "...",
      "version": 1,
      "duplicate": false
    }
  ]
}
```

Поддержка формата на этапе загрузки не проверяется: endpoint сохранит PDF, Office-файлы, таблицы, архивы, аудио, текстовые и неизвестные бинарные форматы. Извлечение текста и таблиц является следующим шагом конвейера; для нового документа уже создаётся запись `document_extractions` со статусом `pending`.

## Конфигурация

Скопируйте `.env.example` в `.env` и измените нужные параметры. Основные ограничения:

- `PROJECTILE_MAX_UPLOAD_SIZE_BYTES` — максимальный размер одного файла, по умолчанию 512 МиБ;
- `PROJECTILE_MAX_FILES_PER_REQUEST` — максимальное количество файлов, по умолчанию 1000;
- `PROJECTILE_CORS_ORIGINS` — разрешённые frontend origins;
- `PROJECTILE_STORAGE_ROOT` — каталог бинарных файлов;
- `PROJECTILE_PROJECT_STAGE_TEMPLATES_PATH` — путь к каталогу этапов;
- `PROJECTILE_PROJECT_WORK_TEMPLATES_PATH` — путь к production-каталогу работ;
- `PROJECTILE_DATABASE_URL` — строка подключения SQLAlchemy/asyncpg.

## Таблицы PostgreSQL

- `projects`;
- `documents`;
- `project_documents`;
- `document_extractions`;
- `processing_runs`;
- `analysis_runs`;
- `project_analyses`;
- `project_types`.

Для MVP схема создаётся при старте приложения, а справочник `data/project-types.json` синхронизируется с таблицей `project_types`.

## Локальные тесты

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,recognition]"
.\.venv\Scripts\python.exe -m pytest
```

Интеграционный тест требует запущенного PostgreSQL:

```powershell
docker compose up -d db
$env:RUN_INTEGRATION_TESTS = "1"
$env:TEST_DATABASE_URL = "postgresql+asyncpg://<user>:<password>@localhost:55432/<database>"
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-tmp
```

## Распознавание и анализ проекта

Загрузка и анализ разделены. После загрузки frontend передаёт только `projectId`:

```http
POST /api/v1/projects/{projectId}/analysis-runs
Content-Type: application/json

{}
```

API фиксирует актуальные версии документов проекта и немедленно возвращает `202`:

```json
{
  "run_id": "d55ba5e4-d516-4918-b53f-346f2970cc2c",
  "project_id": "c9c8908d-e995-4676-8ca7-9c53cd9fc457",
  "status": "queued",
  "document_ids": ["ea513dfd-cb92-46a5-b308-a247897aa50d"]
}
```

Frontend опрашивает состояние отдельным запросом:

```http
GET /api/v1/projects/{projectId}/analysis-runs/{runId}
```

Возможные состояния: `queued`, `extracting`, `analyzing`, `requires_input`, `ready`,
`failed`. Последний запуск также доступен через
`GET /api/v1/projects/{projectId}/analyses/latest`.

Конвейер сначала использует быстрый текстовый слой PyMuPDF для цифровых PDF и нативные
парсеры для DOCX/PPTX. Docling и Tesseract `rus+eng` запускаются только как fallback для
сканов, изображений и документов без достаточного текста. Для русской речи используется
faster-whisper с VAD; CPU-модель по умолчанию — `small`, а `large-v3` можно включить через
`PROJECTILE_RECOGNITION_MODEL`, если точность важнее скорости. ZIP-архивы раскрываются
с ограничениями, обычные текстовые форматы читаются без тяжёлого распознавателя.
Дополнительно поддерживаются RAR/7z,
Excel с сохранением формул, MPP-расписания, VSDX-схемы и аудиодорожки из видео.
Результаты OCR/ASR кэшируются в
`document_extractions`. Итоговая типизация, факты, допущения, пробелы и только существенно
влияющие на оценку вопросы сохраняются в `project_analyses`; состояние задачи — в
`analysis_runs`.

Если суммарный текст пачки не помещается в один запрос модели, worker сначала строит
структурированные конспекты отдельных документов небольшими пакетами, обрабатывает до
`PROJECTILE_ANALYSIS_DIGEST_CONCURRENCY` пакетов параллельно, сохраняет их в
`document_digests`, а затем выполняет общий анализ проекта. Исходные требования имеют
приоритет над КП и внутренними расчётами, но противоречия между ними остаются в `issues`.

Для принудительного повторного OCR/ASR передайте `{"force_reextract": true}`. По умолчанию
используется сохранённый текст. Смысловой анализ запускается через авторизованный
Codex CLI (`codex exec`), без API-ключа. Проверьте авторизацию командой
`codex login status`; название модели настраивается через `PROJECTILE_ANALYSIS_MODEL`.
Для Docker укажите `PROJECTILE_CODEX_HOME` — путь к каталогу `~/.codex` на хосте
(в Windows, например, `C:/Users/username/.codex`).

Результаты замеров и оставшиеся ограничения описаны в
[docs/performance-review.md](docs/performance-review.md).
