import uuid

from app.storage import LocalFileStorage, safe_filename, safe_relative_path


def test_safe_filename_removes_paths_and_unsafe_characters() -> None:
    assert safe_filename("../../folder\\brief:final?.pdf") == "brief_final_.pdf"


def test_safe_filename_has_fallback() -> None:
    assert safe_filename("...") == "upload.bin"


def test_safe_relative_path_preserves_folders_without_traversal() -> None:
    assert safe_relative_path("../ТЗ/этап:1/brief?.docx") == "ТЗ/этап_1/brief_.docx"


def test_generated_bytes_are_persisted_in_project_layout(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path, 1024, 128)
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()

    persisted = storage.persist_bytes(
        b"xlsx-content", project_id, document_id, 2, "Клиент — поддержка.xlsx"
    )

    assert persisted.absolute_path.read_bytes() == b"xlsx-content"
    assert persisted.storage_uri.startswith(f"documents/{project_id}/{document_id}/v2/")
