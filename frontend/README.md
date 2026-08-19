# Projectile frontend

Статический frontend без сборки и runtime-зависимостей. По умолчанию обращается к
backend по адресу `http://localhost:8000`.

Интерфейс построен как чат: история диалогов хранится в backend, сообщения запускают
анализ, а ответы на уточняющие вопросы продолжают его в том же контексте. К готовому
результату можно скачать PDF-отчёт.

## Запуск

Из корня репозитория:

```powershell
python -m http.server 5173 --directory frontend
```

Затем откройте <http://localhost:5173>. Origin уже входит в стандартный список CORS
backend. Backend должен быть запущен отдельно через Docker Compose.

Для другого адреса API измените только `apiBase` в `frontend/config.js`. Секреты и
backend-переменные окружения во frontend не нужны.

## Тесты

Тесты используют встроенный test runner Node.js 20+ и не требуют `npm install`:

```powershell
cd frontend
npm test
```

Без Node.js те же ключевые проверки можно открыть в браузере по адресу
<http://localhost:5173/tests/browser.html> при запущенном статическом сервере.
