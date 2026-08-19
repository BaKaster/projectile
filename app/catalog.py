from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import ProjectType


async def seed_project_types(
    session_factory: async_sessionmaker, catalog_path: Path
) -> None:
    data = json.loads(catalog_path.read_text(encoding="utf-8"))

    async with session_factory.begin() as session:
        for direction in data["directions"]:
            for project_type in direction["project_types"]:
                attributes = {
                    key: value
                    for key, value in project_type.items()
                    if key not in {"code", "name", "details"}
                }
                statement = insert(ProjectType).values(
                    code=project_type["code"],
                    direction_code=direction["code"],
                    name=project_type["name"],
                    details=project_type.get("details"),
                    attributes=attributes,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[ProjectType.code],
                    set_={
                        "direction_code": statement.excluded.direction_code,
                        "name": statement.excluded.name,
                        "details": statement.excluded.details,
                        "attributes": statement.excluded.attributes,
                    },
                )
                await session.execute(statement)
