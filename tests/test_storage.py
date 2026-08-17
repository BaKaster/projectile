from app.storage import safe_filename, safe_relative_path


def test_safe_filename_removes_paths_and_unsafe_characters() -> None:
    assert safe_filename("../../folder\\brief:final?.pdf") == "brief_final_.pdf"


def test_safe_filename_has_fallback() -> None:
    assert safe_filename("...") == "upload.bin"


def test_safe_relative_path_preserves_folders_without_traversal() -> None:
    assert safe_relative_path("../ТЗ/этап:1/brief?.docx") == "ТЗ/этап_1/brief_.docx"
