#!/bin/sh
set -eu

# Projectile uses the authenticated Codex CLI session, not an API key.
unset OPENAI_API_KEY KEY_OPENAI PROJECTILE_OPENAI_API_KEY || true

auth_file="${PROJECTILE_CODEX_AUTH_FILE:-}"
auth_payload="${PROJECTILE_CODEX_AUTH_JSON_B64:-}"

# Railway mounts a fresh persistent volume owned by root. Prepare only the
# application-owned paths here, then drop privileges before starting the API.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/persistent/storage /app/persistent/codex
    chown -R appuser:appuser /app/persistent
    exec gosu appuser "$0" "$@"
fi

if [ -n "$auth_file" ] && [ -n "$auth_payload" ] && [ ! -s "$auth_file" ]; then
    auth_dir=$(dirname "$auth_file")
    auth_tmp="${auth_file}.tmp"
    umask 077
    mkdir -p "$auth_dir"
    printf '%s' "$auth_payload" | base64 -d > "$auth_tmp"
    mv "$auth_tmp" "$auth_file"
fi

unset PROJECTILE_CODEX_AUTH_JSON_B64 || true

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}"
