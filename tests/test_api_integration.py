from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import Settings
from app.database import create_database
from app.main import create_app
from app.models import Document, Project

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 with PostgreSQL available",
)


def test_project_document_upload_is_persisted_and_idempotent(tmp_path: Path) -> None:
    project_id = uuid.uuid4()
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("Set TEST_DATABASE_URL for the integration database")
    settings = Settings(
        database_url=test_database_url,
        storage_root=tmp_path / "storage",
        project_types_path=Path("data/project-types.json"),
        auto_create_schema=True,
        analysis_worker_enabled=False,
    )
    app = create_app(settings)

    first_files = [
        ("files", ("brief.custom", b"custom-format-content", "application/x-custom")),
        ("files", ("notes.txt", "Техническое задание".encode(), "text/plain")),
    ]
    idempotency_key = f"upload-{uuid.uuid4()}"

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "ok"}

        created = client.post(
            "/api/v1/projects", json={"id": str(project_id), "name": "Test project"}
        )
        assert created.status_code == 201

        uploaded = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files=first_files,
            data={"relative_paths": ["requirements/brief.custom", "notes/notes.txt"]},
            headers={"Idempotency-Key": idempotency_key},
        )
        assert uploaded.status_code == 202, uploaded.text
        payload = uploaded.json()
        document_ids = [uuid.UUID(item["id"]) for item in payload["documents"]]
        assert payload["status"] == "uploaded"
        assert len(payload["documents"]) == 2
        assert all(not document["duplicate"] for document in payload["documents"])
        assert payload["documents"][0]["source_path"] == "requirements/brief.custom"

        repeated = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files=first_files,
            data={"relative_paths": ["requirements/brief.custom", "notes/notes.txt"]},
            headers={"Idempotency-Key": idempotency_key},
        )
        assert repeated.status_code == 202
        assert repeated.json()["upload_run_id"] == payload["upload_run_id"]
        assert all(document["duplicate"] for document in repeated.json()["documents"])

        duplicate_content = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files=[("files", ("renamed.bin", b"custom-format-content"))],
        )
        assert duplicate_content.status_code == 202
        assert duplicate_content.json()["documents"][0]["duplicate"] is True

        conflict = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files=[("files", ("other.bin", b"different-content"))],
            headers={"Idempotency-Key": idempotency_key},
        )
        assert conflict.status_code == 409

        analysis = client.post(
            f"/api/v1/projects/{project_id}/analysis-runs", json={}
        )
        assert analysis.status_code == 202, analysis.text
        analysis_payload = analysis.json()
        assert analysis_payload["status"] == "queued"
        assert len(analysis_payload["document_ids"]) == 2

        run = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{analysis_payload['run_id']}"
        )
        assert run.status_code == 200
        assert run.json()["status"] == "queued"
        assert run.json()["result"] is None

        latest = client.get(f"/api/v1/projects/{project_id}/analyses/latest")
        assert latest.status_code == 200
        assert latest.json()["run_id"] == analysis_payload["run_id"]

    stored_files = [
        path
        for path in settings.storage_root.rglob("*")
        if path.is_file() and path.parent.name != ".staging"
    ]
    assert len(stored_files) == 2

    async def cleanup() -> None:
        engine, session_factory = create_database(settings)
        async with session_factory.begin() as session:
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.execute(delete(Document).where(Document.id.in_(document_ids)))
        await engine.dispose()

    asyncio.run(cleanup())
