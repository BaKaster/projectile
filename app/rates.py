from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LaborRole, RateImport, RoleRate


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


def split_rate_update_text(text: str, items: list[dict]) -> str:
    """Return the part of a chat message that is not a recognised rate row."""
    rate_lines = {str(item["source"]).strip() for item in items}
    return "\n".join(
        line for line in text.splitlines() if line.strip() not in rate_lines
    ).strip()


def project_input_error(text: str) -> str | None:
    """Reject obvious non-descriptions before wasting an analysis run on them."""
    normalized = re.sub(r"\s+", " ", text).strip()
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_-]*", normalized)
    letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", normalized)
    if len(normalized) < 12 or len(words) < 3 or len(letters) < 8:
        return (
            "Недостаточно данных для анализа. Опишите проект хотя бы одним "
            "предложением: цель, систему или услугу, объём и ожидаемый результат."
        )
    if len(set(letters.casefold())) <= 2:
        return "Похоже на случайный текст. Опишите проект или приложите документ."
    return None


async def apply_rates_from_text(
    session: AsyncSession, text: str, *, source_name: str
) -> int:
    """Apply only exact role-ID matches found in normal project input."""
    roles = list((await session.scalars(select(LaborRole))).all())
    items = parse_rate_text(text, roles)
    if not items:
        return 0
    imported = RateImport(
        filename=source_name[:512], status="applied", auto_apply=True,
        extracted_items=items, applied_count=len(items),
    )
    session.add(imported)
    await session.flush()
    now = datetime.now(UTC)
    for item in items:
        session.add(RoleRate(
            role_code=item["role_code"], sale_rate=item["sale_rate"],
            cost_rate=item["cost_rate"], effective_from=now,
            source_import_id=imported.id,
        ))
    return len(items)
