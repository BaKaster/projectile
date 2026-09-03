from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.codex_cli import (
    CodexCliClient,
    CodexCliError,
    _codex_environment,
    _failure_summary,
    codex_cli_available,
)


class ExampleResult(BaseModel):
    answer: str


def test_failure_summary_keeps_usage_limit_and_omits_echoed_prompt() -> None:
    stderr = (
        b"user\nconfidential project text\n"
        b"ERROR: You've hit your usage limit. Try again at Aug 29th, 2026 3:02 PM.\n"
    )

    summary = _failure_summary(b"", stderr)

    assert "usage limit" in summary
    assert "confidential" not in summary


def test_failure_summary_sanitizes_authentication_errors() -> None:
    summary = _failure_summary(
        b"",
        b"user\nconfidential project text\nERROR: HTTP error: 401 Unauthorized\n",
    )

    assert summary == "Codex authentication failed (401 Unauthorized)"
    assert "confidential" not in summary


def test_codex_cli_uses_agents_instruction_and_output_schema(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_execute(
        self,
        *,
        prompt: str,
        workspace: Path,
        schema_path: Path,
        output_path: Path,
        codex_home: Path | None,
    ) -> str:
        captured["prompt"] = prompt
        captured["agents"] = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        return '{"answer":"ok"}'

    monkeypatch.setattr(CodexCliClient, "_execute", fake_execute)
    response = asyncio.run(
        CodexCliClient(model="test-model").parse(
            input=[
                {"role": "system", "content": "System rule"},
                {"role": "user", "content": "Untrusted document"},
            ],
            text_format=ExampleResult,
        )
    )

    assert response.output_parsed.answer == "ok"
    assert captured["prompt"] == "Untrusted document"
    assert "System rule" in str(captured["agents"])
    assert captured["schema"]["properties"]["answer"]["type"] == "string"
    assert captured["schema"]["additionalProperties"] is False
    assert captured["schema"]["required"] == ["answer"]


def test_codex_cli_rejects_output_outside_contract(monkeypatch) -> None:
    async def fake_execute(self, **kwargs) -> str:
        return '{"wrong":"value"}'

    monkeypatch.setattr(CodexCliClient, "_execute", fake_execute)

    with pytest.raises(CodexCliError, match="does not match ExampleResult"):
        asyncio.run(
            CodexCliClient(model="test-model").parse(
                input=[{"role": "user", "content": "request"}],
                text_format=ExampleResult,
            )
        )


def test_codex_child_environment_excludes_application_secrets(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROJECTILE_DATABASE_URL", "secret-database-url")
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-reach-codex-cli")
    monkeypatch.setenv("PATH", "safe-path")

    environment = _codex_environment(tmp_path)

    assert environment["PATH"] == "safe-path"
    assert environment["CODEX_HOME"] == str(tmp_path)
    assert "PROJECTILE_DATABASE_URL" not in environment
    assert "OPENAI_API_KEY" not in environment


def test_codex_child_environment_accepts_explicit_api_key(tmp_path: Path) -> None:
    environment = _codex_environment(tmp_path, api_key="test-api-key")

    assert environment["CODEX_API_KEY"] == "test-api-key"


def test_persistent_auth_uses_source_codex_home(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens":{}}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    client = CodexCliClient(
        model="test-model", auth_file=auth_file, persist_auth_file=True
    )
    codex_home = client._prepare_codex_home(runtime_root)
    assert codex_home == tmp_path.resolve()


def test_ephemeral_auth_still_copies_source_file(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens":{}}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    client = CodexCliClient(model="test-model", auth_file=auth_file)
    codex_home = client._prepare_codex_home(runtime_root)
    assert codex_home is not None
    assert codex_home != tmp_path
    assert (codex_home / "auth.json").read_text(encoding="utf-8") == '{"tokens":{}}'


def test_codex_cli_available_checks_login_without_auth_file(
    monkeypatch, tmp_path: Path
) -> None:
    executable = tmp_path / "codex"
    executable.touch()
    captured: dict[str, object] = {}

    class Status:
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Status()

    monkeypatch.setattr("app.codex_cli.resolve_codex_cli", lambda value: executable)
    monkeypatch.setattr("app.codex_cli.subprocess.run", fake_run)

    assert codex_cli_available("codex", None) is True
    assert captured["command"] == [str(executable), "login", "status"]
    assert captured["kwargs"]["timeout"] == 5


def test_codex_cli_available_rejects_logged_out_session(
    monkeypatch, tmp_path: Path
) -> None:
    executable = tmp_path / "codex"
    executable.touch()

    class Status:
        returncode = 1

    monkeypatch.setattr("app.codex_cli.resolve_codex_cli", lambda value: executable)
    monkeypatch.setattr(
        "app.codex_cli.subprocess.run", lambda command, **kwargs: Status()
    )

    assert codex_cli_available("codex", None) is False
