"""Roster queries: clans, members (people) and accounts (player tags)."""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from astra.tags import normalize_tag


@dataclass(frozen=True)
class ImportResult:
    members_created: int
    accounts_upserted: int
    skipped_rows: list[str]


class RosterRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_clan(self, tag: str, name: str, short_name: str) -> None:
        await self._pool.execute(
            """INSERT INTO clans (tag, name, short_name) VALUES ($1, $2, $3)
               ON CONFLICT (tag) DO UPDATE SET name = EXCLUDED.name, short_name = EXCLUDED.short_name""",
            normalize_tag(tag), name, short_name,
        )

    async def list_account_tags(self) -> list[str]:
        """Accounts owned by a known person: the set the fetchers track."""
        rows = await self._pool.fetch(
            "SELECT tag FROM accounts WHERE member_id IS NOT NULL ORDER BY tag"
        )
        return [r["tag"] for r in rows]

    async def import_player_tags(self, rows: list[list[str]]) -> ImportResult:
        """One-time import of the old 'Player Tags' sheet: columns are
        (tag, account name, owner name). Safe to run more than once.

        All rows go in one transaction: either the whole sheet imports or
        nothing does, so a bad row can't leave a half-imported roster.
        """
        created = upserted = 0
        skipped: list[str] = []
        async with self._pool.acquire() as conn, conn.transaction():
            member_ids: dict[str, int] = {}
            for i, row in enumerate(rows, start=2):  # row 1 is the header
                cells = [c.strip() for c in row[:3]] + [""] * (3 - len(row[:3]))
                raw_tag, account_name, owner = cells
                if not raw_tag or not owner:
                    skipped.append(f"row {i}: missing tag or owner")
                    continue
                try:
                    tag = normalize_tag(raw_tag)
                except ValueError as e:
                    skipped.append(f"row {i}: {e}")
                    continue

                if owner not in member_ids:
                    existing = await conn.fetchval(
                        "SELECT id FROM members WHERE display_name = $1 ORDER BY id LIMIT 1", owner
                    )
                    if existing is None:
                        existing = await conn.fetchval(
                            "INSERT INTO members (display_name) VALUES ($1) RETURNING id", owner
                        )
                        created += 1
                    member_ids[owner] = existing

                await conn.execute(
                    """INSERT INTO accounts (tag, member_id, name) VALUES ($1, $2, $3)
                       ON CONFLICT (tag) DO UPDATE
                       SET member_id = EXCLUDED.member_id, name = EXCLUDED.name, updated_at = now()""",
                    tag, member_ids[owner], account_name or tag,
                )
                upserted += 1
        return ImportResult(created, upserted, skipped)
