from app.analyzer import PROMPT_VERSION
from app.models import ProjectAnalysis


def test_prompt_version_fits_persisted_column() -> None:
    column = ProjectAnalysis.__table__.c.prompt_version

    assert column.type.length is not None
    assert column.type.length >= len(PROMPT_VERSION)
