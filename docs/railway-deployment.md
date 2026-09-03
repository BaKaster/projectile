# Railway deployment with Codex CLI

Projectile keeps semantic analysis behind the existing non-interactive
`codex exec` adapter. Production authentication comes from the same ChatGPT
Codex session used on the operator workstation; no OpenAI Platform API key is
required.

## Services

Create one Railway project containing:

1. A PostgreSQL database service.
2. A Projectile service built from the repository root and the `dev` branch.
3. A Railway volume attached to Projectile at `/app/persistent`.

Generate a public domain for the Projectile service after its first healthy
deployment.

## Projectile variables

Configure these variables on the Projectile service:

```dotenv
PROJECTILE_DATABASE_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}
PROJECTILE_STORAGE_ROOT=/app/persistent/storage
PROJECTILE_CODEX_AUTH_FILE=/app/persistent/codex/auth.json
PROJECTILE_CODEX_PERSIST_AUTH_FILE=true
PROJECTILE_CODEX_AUTH_JSON_B64=<base64 encoded auth.json>
PROJECTILE_CODEX_AUTH_OVERWRITE=false
PROJECTILE_AUTO_CREATE_SCHEMA=true
PROJECTILE_ANALYSIS_WORKER_ENABLED=true
PROJECTILE_EXCEL_RECALCULATION_COMMAND=/usr/bin/libreoffice
PROJECTILE_CORS_ORIGINS=<frontend origin>
PROJECTILE_ANALYSIS_MODEL=gpt-5.4
PROJECTILE_ANALYSIS_REASONING_EFFORT=low
PORT=8000
```

The bootstrap payload is written only when the persistent `auth.json` does not
exist. Later token refreshes are retained on the volume and are not overwritten
by redeployments.

If Codex reports a 401, refresh the bootstrap value from a machine where
`codex login status` succeeds, set `PROJECTILE_CODEX_AUTH_OVERWRITE=true` for
one deployment, then return it to `false`. This replaces only the expired
Codex session and leaves project data on the volume untouched.

Generate the bootstrap value locally without printing the decoded credential:

```powershell
$authPath = Join-Path $env:USERPROFILE ".codex\auth.json"
[Convert]::ToBase64String([IO.File]::ReadAllBytes($authPath)) |
    Set-Clipboard
```

Paste the clipboard value into Railway as the secret
`PROJECTILE_CODEX_AUTH_JSON_B64`. Never commit `auth.json` or its base64 form.

## Verification

After deployment:

1. Open `/health` and confirm `{"status":"ok","database":"ok"}`.
2. Open a Railway shell and run `codex login status` with
   `CODEX_HOME=/app/persistent/codex`.
3. Upload a small text document and confirm that an analysis run completes.
4. Confirm that generated Excel files remain available after a redeploy.
