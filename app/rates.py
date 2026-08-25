from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LaborRole, RoleRate


def seed_roles(catalog_roles: list[dict]) -> list[LaborRole]:
    return [LaborRole(code=item["code"], name=item["name"], external_id=item.get("external_id")) for item in catalog_roles]


async def ensure_rate_catalog(session: AsyncSession, catalog_roles: list[dict]) -> None:
    existing = {item.code for item in (await session.scalars(select(LaborRole))).all()}
    for item in catalog_roles:
        if item["code"] not in existing:
            session.add(LaborRole(code=item["code"], name=item["name"], external_id=item.get("external_id")))
    await session.flush()
    has_rates = (await session.scalars(select(RoleRate).limit(1))).first()
    if not has_rates:
        now = datetime.now(UTC)
        for item in catalog_roles:
            session.add(RoleRate(role_code=item["code"], sale_rate=round(item["sale_rate"]), cost_rate=round(item["cost_rate"]), effective_from=now))
    await session.commit()


async def current_rates(session: AsyncSession) -> dict[str, tuple[int, int]]:
    rows = (await session.scalars(select(RoleRate).order_by(RoleRate.role_code, RoleRate.effective_from.desc()))).all()
    result: dict[str, tuple[int, int]] = {}
    for row in rows:
        result.setdefault(row.role_code, (row.sale_rate, row.cost_rate))
    return result


def parse_rate_text(text: str, roles: list[LaborRole]) -> list[dict]:
    """Conservative OCR table parser. Unmatched/ambiguous values stay in review."""
    by_id = {str(role.external_id): role for role in roles if role.external_id is not None}
    result: list[dict] = []
    for line in text.splitlines():
        numbers = [int(value.replace(" ", "")) for value in re.findall(r"(?<!\d)(\d[\d ]{2,7})(?!\d)", line)]
        ids = re.findall(r"#(\d+)", line)
        role = by_id.get(ids[-1]) if ids else None
        if role is None or len(numbers) < 2:
            continue
        sale, cost = numbers[-2:]
        if not (100 <= sale <= 1_000_000 and 100 <= cost <= 1_000_000):
            continue
        result.append({"role_code": role.code, "role_name": role.name, "external_id": role.external_id, "sale_rate": sale, "cost_rate": cost, "confidence": 0.99, "source": line.strip(), "selected": True, "eligible_for_auto_apply": True})
    return result
