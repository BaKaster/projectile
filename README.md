# Projectile

Backend для загрузки проектных документов, их последующего анализа и формирования предварительной оценки проекта.

## Что реализовано

- FastAPI и интерактивная документация OpenAPI;
- создание проекта;
- загрузка одного или нескольких файлов любого формата;
- потоковое чтение файлов без загрузки всего содержимого в память приложения;
- SHA-256, дедупликация неизменённых файлов и версии одноимённых документов;
- идемпотентность запросов через `Idempotency-Key`;
- PostgreSQL для проектов, метаданных документов, связей и запусков обработки;
- локальное файловое хранилище для бинарников;
- автоматическая загрузка 26 корпоративных типов проектов в `project_types`.

Бинарное содержимое намеренно не кладётся в PostgreSQL. В БД сохраняются метаданные и `storage_uri`, а сам файл находится в `storage/`, как предусмотрено архитектурой.

## Запуск через Docker

```powershell
docker compose up -d --build
```

После запуска:

- API: `http://localhost:8000`;
- Swagger UI: `http://localhost:8000/docs`;
- health check: `http://localhost:8000/health`;
- PostgreSQL с хоста: `localhost:55432`;
- база, пользователь и пароль по умолчанию: `projectile`.

Порт `55432` выбран для PostgreSQL, чтобы не конфликтовать с локальным PostgreSQL на стандартном порту `5432`. Его можно изменить через `POSTGRES_PORT`.

Остановить сервисы:

```powershell
docker compose down
```

Данные PostgreSQL сохраняются в Docker volume `projectile_postgres_data`. Команда `docker compose down` их не удаляет.

## API

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

Успешный ответ имеет статус `202 Accepted`:

```json
{
  "project_id": "c9c8908d-e995-4676-8ca7-9c53cd9fc457",
  "run_id": "fb087390-f84e-42bc-ab48-48704a3b543d",
  "status": "uploaded",
  "documents": [
    {
      "id": "ea513dfd-cb92-46a5-b308-a247897aa50d",
      "original_filename": "ТЗ.pdf",
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
- `PROJECTILE_MAX_FILES_PER_REQUEST` — максимальное количество файлов, по умолчанию 100;
- `PROJECTILE_CORS_ORIGINS` — разрешённые frontend origins;
- `PROJECTILE_STORAGE_ROOT` — каталог бинарных файлов;
- `PROJECTILE_DATABASE_URL` — строка подключения SQLAlchemy/asyncpg.

## Таблицы PostgreSQL

- `projects`;
- `documents`;
- `project_documents`;
- `document_extractions`;
- `processing_runs`;
- `project_types`.

Для MVP схема создаётся при старте приложения, а справочник `data/project-types.json` синхронизируется с таблицей `project_types`.

## Локальные тесты

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

Интеграционный тест требует запущенного PostgreSQL:

```powershell
docker compose up -d db
$env:RUN_INTEGRATION_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-tmp
```
