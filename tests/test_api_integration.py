from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 with PostgreSQL available",
)


def test_project_document_upload_is_persisted_and_idempotent(tmp_path: Path) -> None:
    project_id = uuid.uuid4()
    settings = Settings(
        database_url=os.getenv(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://projectile:projectile@localhost:55432/projectile",
        ),
        storage_root=tmp_path / "storage",
        project_types_path=Path("data/project-types.json"),
        auto_create_schema=True,
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
            headers={"Idempotency-Key": idempotency_key},
        )
        assert uploaded.status_code == 202, uploaded.text
        payload = uploaded.json()
        assert payload["status"] == "uploaded"
        assert len(payload["documents"]) == 2
        assert all(not document["duplicate"] for document in payload["documents"])

        repeated = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files=first_files,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert repeated.status_code == 202
        assert repeated.json()["run_id"] == payload["run_id"]
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

    stored_files = [
        path
        for path in settings.storage_root.rglob("*")
        if path.is_file() and path.parent.name != ".staging"
    ]
    assert len(stored_files) == 2
