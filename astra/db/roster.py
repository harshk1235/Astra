"""Roster queries: clans, members (people) and accounts (player tags)."""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from astra.roles.rules import Rank
from astra.tags import normalize_tag

# In-game role labels as they appear in the members sheet.
_ROLE_LABELS = {
    "mem": Rank.MEMBER, "member": Rank.MEMBER,
    "eld": Rank.ELDER, "elder": Rank.ELDER, "admin": Rank.ELDER,
    "co": Rank.CO_LEADER, "co-leader": Rank.CO_LEADER, "coleader": Rank.CO_LEADER,
    "lead": Rank.LEADER, "leader": Rank.LEADER,
}
_RANK_DB = {Rank.MEMBER: "member", Rank.ELDER: "elder", Rank.CO_LEADER: "co_leader", Rank.LEADER: "leader"}
_MEMBERS_SHEET_COLUMNS = ("DisplayName", "Username", "ID", "Name", "Tag", "Verified", "Clan Tag", "Role")


def parse_role(label: str) -> Rank | None:
    return _ROLE_LABELS.get(label.strip().lower())


@dataclass(frozen=True)
class ImportResult:
    members_created: int
    accounts_upserted: int
    skipped_rows: list[str]
    memberships_upserted: int = 0


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

    async def import_members_sheet(self, rows: list[list[str]]) -> ImportResult:
        """Import the members sheet (one row per linked account):
        DisplayName | Username | ID | Name | Tag | Verified | Clan | Clan Tag | Role

        - People are keyed by Discord user ID, so re-running updates instead
          of duplicating.
        - Every row's account is linked to its owner, even alts that aren't in
          a tracked clan right now.
        - A person's rank in a clan is their highest-ranked account there
          (Member + Co-leader accounts in PR -> Co-leader in PR). Only clans in
          the clans table are imported; others (e.g. a non-family clan) are
          recorded as the account's in-game clan only.
        Columns are found by header name, so their order doesn't matter.
        """
        if not rows:
            return ImportResult(0, 0, ["sheet is empty"])
        header = [h.strip() for h in rows[0]]
        missing = [c for c in _MEMBERS_SHEET_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"Members sheet is missing columns: {', '.join(missing)}")
        col = {name: header.index(name) for name in _MEMBERS_SHEET_COLUMNS}

        def cell(row: list[str], name: str) -> str:
            i = col[name]
            return row[i].strip() if i < len(row) else ""

        created = upserted = 0
        skipped: list[str] = []
        best_rank: dict[tuple[int, str], Rank] = {}
        async with self._pool.acquire() as conn, conn.transaction():
            tracked = {r["tag"] for r in await conn.fetch("SELECT tag FROM clans WHERE active")}
            for i, row in enumerate(rows[1:], start=2):
                discord_id, raw_tag = cell(row, "ID"), cell(row, "Tag")
                if not discord_id.isdigit():
                    skipped.append(f"row {i}: missing or invalid Discord ID")
                    continue
                try:
                    tag = normalize_tag(raw_tag)
                except ValueError as e:
                    skipped.append(f"row {i}: {e}")
                    continue
                # "Voidmx | PR" -> "Voidmx"; fall back to the username.
                display = cell(row, "DisplayName").split(" | ")[0].strip() or cell(row, "Username") or discord_id

                member_id, inserted = await conn.fetchrow(
                    """INSERT INTO members (display_name, discord_user_id) VALUES ($1, $2)
                       ON CONFLICT (discord_user_id) DO UPDATE SET display_name = EXCLUDED.display_name
                       RETURNING id, (xmax = 0) AS inserted""",
                    display, int(discord_id),
                )
                created += int(inserted)

                clan_tag = cell(row, "Clan Tag")
                try:
                    clan_tag = normalize_tag(clan_tag) if clan_tag else None
                except ValueError:
                    clan_tag = None
                await conn.execute(
                    """INSERT INTO accounts (tag, member_id, name, ingame_clan_tag, verified)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (tag) DO UPDATE
                       SET member_id = EXCLUDED.member_id, name = EXCLUDED.name,
                           ingame_clan_tag = EXCLUDED.ingame_clan_tag,
                           verified = EXCLUDED.verified, updated_at = now()""",
                    tag, member_id, cell(row, "Name") or tag, clan_tag,
                    cell(row, "Verified").lower() in ("yes", "true", "y"),
                )
                upserted += 1

                if clan_tag in tracked:
                    rank = parse_role(cell(row, "Role"))
                    if rank is None:
                        skipped.append(f"row {i}: unknown role {cell(row, 'Role')!r}; account linked, no membership")
                    else:
                        key = (member_id, clan_tag)
                        best_rank[key] = max(rank, best_rank.get(key, rank))

            for (member_id, clan_tag), rank in best_rank.items():
                await conn.execute(
                    """INSERT INTO clan_memberships (member_id, clan_tag, rank, approved_by)
                       VALUES ($1, $2, $3, NULL)
                       ON CONFLICT (member_id, clan_tag) DO UPDATE SET rank = EXCLUDED.rank""",
                    member_id, clan_tag, _RANK_DB[rank],
                )
        return ImportResult(created, upserted, skipped, len(best_rank))
