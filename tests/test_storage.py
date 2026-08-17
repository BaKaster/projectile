from app.storage import safe_filename


def test_safe_filename_removes_paths_and_unsafe_characters() -> None:
    assert safe_filename("../../folder\\brief:final?.pdf") == "brief_final_.pdf"


def test_safe_filename_has_fallback() -> None:
    assert safe_filename("...") == "upload.bin"
