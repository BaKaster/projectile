# Передача backend frontend-разработчику

## Что нужно для запуска

- Git;
- Docker Desktop с Docker Compose;
- свободные порты `8000` и `55432`;
- интернет при первом запуске для скачивания образов и ML-моделей;
- установленный и авторизованный Codex CLI для смысловой классификации.

Python, PostgreSQL, Java, FFmpeg, Tesseract и библиотеки распознавания локально
устанавливать не нужно: они находятся в Docker-образе.

```powershell
Copy-Item .env.example .env
```

В `.env` нужно:

1. заменить `POSTGRES_PASSWORD` на длинный случайный пароль;
2. указать тот же пароль внутри `PROJECTILE_DATABASE_URL`;
3. указать `PROJECTILE_CODEX_HOME` — путь к каталогу авторизованного Codex CLI;
4. при необходимости добавить origin frontend в `PROJECTILE_CORS_ORIGINS`.

Файл `.env` не коммитится. Значения из него нельзя вставлять во frontend-код,
логи или публичные переменные сборки.

Запуск:

```powershell
docker compose up -d --build
docker compose ps
```

Первая сборка тяжёлая из-за OCR/ASR-зависимостей. Следующие сборки после изменения только
Python-кода используют Docker cache и должны быть заметно быстрее. Цифровые PDF, DOCX и
PPTX обычно извлекаются быстро; сканы и аудио на CPU могут обрабатываться существенно
дольше, поэтому frontend не должен задавать жёсткий таймаут всему анализу.

Проверка:

- health: <http://localhost:8000/health>;
- Swagger: <http://localhost:8000/docs>;
- OpenAPI: <http://localhost:8000/openapi.json>;
- логи: `docker compose logs -f api`.

Остановка без удаления данных: `docker compose down`. Не использовать
`docker compose down -v`, если базу и кэш моделей нужно сохранить.

## Контракт для frontend

Источник истины — работающая схема `/openapi.json` и модели в
`app/schemas.py`. Не придумывать отсутствующие endpoint-ы.

1. Чатовый сценарий использует `POST /api/v1/chats`, `GET /api/v1/chats` и
   `GET /api/v1/chats/{chatId}`. Идентификатор чата совпадает с `projectId`.
   Сообщение отправляется через `POST /api/v1/chats/{chatId}/messages` с
   `{ "content": "..." }`; ответ содержит сообщение и новый `run_id` анализа.
2. `POST /api/v1/projects/{projectId}/documents` как `multipart/form-data`.
   Каждый файл добавляется повторяемым полем `files`. Для загрузки папки для
   каждого файла добавить соответствующее поле `relative_paths` в том же порядке.
   Заголовок `Content-Type` вручную не устанавливать: boundary создаёт браузер.
   `upload_run_id` относится только к загрузке и не используется для polling.
3. `POST /api/v1/projects/{projectId}/analysis-runs` с `{}`. Сохранить
   возвращённый `run_id` — это идентификатор анализа.
4. Опросить `GET /api/v1/projects/{projectId}/analysis-runs/{runId}` раз в
   3–5 секунд. Остановить polling при размонтировании страницы и при терминальном
   статусе.
5. `queued`, `extracting`, `analyzing` — анализ продолжается, `result` может быть
   `null`. `ready` и `requires_input` — результат готов. `failed` — показать
   содержимое `errors` и возможность повторного запуска.
6. В результате показать `project_type_code`, `confidence`, `summary`,
   `rationale`, `facts`, `assumptions`, `issues`, `questions` и `warnings`.
   `project_type_code` может быть `null`, если документы действительно
   неоднозначны. Уверенность отображается только как `low`, `medium`, `high`.
7. При перезагрузке страницы восстановить последний запуск через
   `GET /api/v1/projects/{projectId}/analyses/latest`.
8. Ответ на уточняющие вопросы отправляется через
   `POST /api/v1/projects/{projectId}/analysis-runs/{runId}/answers` с
   `{ "content": "..." }`. Endpoint создаёт новый запуск анализа.
9. Пропуск вопросов: `POST /api/v1/projects/{projectId}/analysis-runs/{runId}/questions/skip`.
10. PDF готового результата:
    `GET /api/v1/projects/{projectId}/analysis-runs/{runId}/report.pdf`.

Ответы принимаются только для запуска в статусе `requires_input`; пропустить вопросы
можно только в том же статусе. `ready` и `requires_input` доступны для скачивания PDF.

Пример загрузки:

```ts
const form = new FormData();

for (const file of files) {
  form.append("files", file);
}

if (files.every((file) => file.webkitRelativePath)) {
  for (const file of files) {
    form.append("relative_paths", file.webkitRelativePath);
  }
}

await fetch(`${apiBase}/api/v1/projects/${projectId}/documents`, {
  method: "POST",
  headers: { "Idempotency-Key": crypto.randomUUID() },
  body: form,
});
```

Важно: если передаётся хотя бы один `relative_paths`, их количество должно быть
равно количеству `files`. Для обычного выбора отдельных файлов проще не передавать
`relative_paths` вообще.

## Готовый промпт для нейронки frontend-разработчика

```text
У тебя есть доступ к репозиторию Projectile. Реализуй frontend для существующего
FastAPI backend. Сначала полностью изучи README.md, docs/frontend-handoff.md,
app/schemas.py, app/api.py и работающий http://localhost:8000/openapi.json.
OpenAPI является источником истины. Backend-контракт не изменяй и отсутствующие
endpoint-ы не придумывай.

Сохрани существующий frontend-стек, архитектуру, дизайн-систему, линтеры и стиль
кода. Если frontend ещё отсутствует, сначала предложи минимальный стек и структуру,
не добавляя лишних зависимостей.

Реализуй полный сценарий:
1. Создание проекта и сохранение projectId.
2. Выбор нескольких файлов и папки. Отправка multipart с повторяемым полем files.
   relative_paths передавай для всех файлов или не передавай вообще. Никогда не
   задавай Content-Type для FormData вручную.
3. После успешной загрузки запусти POST analysis-runs. Не путай upload_run_id из
   загрузки с run_id анализа.
4. Polling анализа каждые 3–5 секунд с отменой при unmount. После перезагрузки
   восстанавливай состояние через analyses/latest.
5. Для queued/extracting/analyzing показывай понятный прогресс. Для ready и
   requires_input обязательно показывай result. Для failed показывай errors и
   кнопку повторного запуска.
6. Отобрази тип проекта, low/medium/high confidence без процентов, summary,
   rationale, факты, допущения, проблемы, вопросы и предупреждения.
7. project_type_code и result до завершения могут быть null — обработай это без
   падения интерфейса. requires_input является успешным терминальным результатом,
   а не ошибкой.
8. Сохраняй русские имена файлов и UTF-8. Добавь состояния загрузки, пустого
   проекта, сетевой ошибки и повторной отправки. Не помещай `.codex/auth.json`,
   PROJECTILE_DATABASE_URL или пароль PostgreSQL во frontend.
9. Базовый URL backend бери из frontend env-переменной. Для локальной разработки
   используй http://localhost:8000.
10. Добавь тесты минимум для FormData, polling до ready/requires_input, failed и
    восстановления analyses/latest.

Перед изменениями кратко перечисли найденные frontend-файлы и план. После работы
запусти доступные typecheck, lint и тесты и сообщи результаты вместе со списком
изменённых файлов.
```
