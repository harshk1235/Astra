"""Clans (with aliases and Discord roles), people, linked accounts, clan
memberships with ranks, and the audit log. All writes for leader commands go
through here."""
from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg

from astra.roles.rules import Membership, Rank, RoleMap
from astra.tags import normalize_tag

RANK_TO_DB = {Rank.MEMBER: "member", Rank.ELDER: "elder", Rank.CO_LEADER: "co_leader", Rank.LEADER: "leader"}
DB_TO_RANK = {v: k for k, v in RANK_TO_DB.items()}


@dataclass(frozen=True)
class Clan:
    tag: str
    name: str
    alias: str            # short name, e.g. PR, used in role names and commands


@dataclass(frozen=True)
class Account:
    tag: str
    name: str
    member_id: int | None
    owner_discord_id: int | None
    owner_name: str | None
    th_level: int | None
    ingame_clan_tag: str | None


@dataclass
class ClanPerson:
    member_id: int
    display_name: str
    discord_user_id: int | None
    rank: Rank
    accounts: list[Account] = field(default_factory=list)


class MembershipRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # --- clans ---------------------------------------------------------------

    async def list_clans(self) -> list[Clan]:
        rows = await self._pool.fetch("SELECT tag, name, short_name FROM clans WHERE active ORDER BY short_name")
        return [Clan(r["tag"], r["name"], r["short_name"]) for r in rows]

    async def find_clan(self, alias_or_tag: str) -> Clan | None:
        """By alias (case-insensitive) or by tag."""
        value = alias_or_tag.strip()
        row = await self._pool.fetchrow(
            "SELECT tag, name, short_name FROM clans WHERE active AND (lower(short_name) = lower($1) OR tag = $2)",
            value, ("#" + value.lstrip("#")).upper(),
        )
        return Clan(row["tag"], row["name"], row["short_name"]) if row else None

    async def add_clan(self, tag: str, name: str, alias: str) -> Clan:
        """Add (or re-activate / rename) a clan. Every clan's Leader rank maps
        to the one shared Leader role, so a new clan inherits it if it's set."""
        tag = normalize_tag(tag)
        async with self._pool.acquire() as conn, conn.transaction():
            taken = await conn.fetchval(
                "SELECT tag FROM clans WHERE lower(short_name) = lower($1) AND tag <> $2", alias, tag)
            if taken:
                raise ValueError(f"The alias {alias} is already used by {taken}.")
            await conn.execute(
                """INSERT INTO clans (tag, name, short_name, active) VALUES ($1, $2, $3, TRUE)
                   ON CONFLICT (tag) DO UPDATE SET name = EXCLUDED.name, short_name = EXCLUDED.short_name,
                                                 active = TRUE""",
                tag, name, alias,
            )
            leader_role = await conn.fetchval(
                "SELECT discord_role_id FROM clan_roles WHERE rank = 'leader' LIMIT 1")
            if leader_role:
                await conn.execute(
                    """INSERT INTO clan_roles (clan_tag, rank, discord_role_id) VALUES ($1, 'leader', $2)
                       ON CONFLICT (clan_tag, rank) DO NOTHING""", tag, leader_role)
        return Clan(tag, name, alias)

    async def deactivate_clan(self, tag: str) -> None:
        """Stop tracking a clan. Its data and memberships stay (for history), but it
        no longer appears in commands, fetchers or role sync."""
        await self._pool.execute("UPDATE clans SET active = FALSE WHERE tag = $1", tag)

    async def set_clan_role(self, clan_tag: str, rank: Rank, role_id: int) -> None:
        await self._pool.execute(
            """INSERT INTO clan_roles (clan_tag, rank, discord_role_id) VALUES ($1, $2, $3)
               ON CONFLICT (clan_tag, rank) DO UPDATE SET discord_role_id = EXCLUDED.discord_role_id""",
            clan_tag, RANK_TO_DB[rank], role_id,
        )

    async def set_leader_role(self, role_id: int) -> None:
        """The Leader role is shared by every clan."""
        await self._pool.execute(
            """INSERT INTO clan_roles (clan_tag, rank, discord_role_id)
               SELECT tag, 'leader', $1 FROM clans
               ON CONFLICT (clan_tag, rank) DO UPDATE SET discord_role_id = EXCLUDED.discord_role_id""",
            role_id,
        )

    async def role_map(self) -> RoleMap:
        rows = await self._pool.fetch(
            """SELECT r.clan_tag, r.rank::text AS rank, r.discord_role_id
               FROM clan_roles r JOIN clans c ON c.tag = r.clan_tag WHERE c.active""")
        result: RoleMap = {}
        for r in rows:
            result.setdefault(r["clan_tag"], {})[DB_TO_RANK[r["rank"]]] = r["discord_role_id"]
        return result

    async def all_managed_role_ids(self) -> set[int]:
        """Every role the bot manages, including those of deactivated clans (so a
        removed clan's roles get taken away on the next refresh) and the common role."""
        ids = {r["discord_role_id"] for r in await self._pool.fetch("SELECT discord_role_id FROM clan_roles")}
        common = await self.common_role_id()
        return ids | ({common} if common else set())

    # --- bot-wide settings -----------------------------------------------------

    async def get_setting(self, key: str) -> str | None:
        return await self._pool.fetchval("SELECT value FROM bot_settings WHERE key = $1", key)

    async def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            await self._pool.execute("DELETE FROM bot_settings WHERE key = $1", key)
        else:
            await self._pool.execute(
                """INSERT INTO bot_settings (key, value) VALUES ($1, $2)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", key, value)

    async def common_role_id(self) -> int | None:
        """The role everyone in any tracked clan gets (like "[ArIX] Member")."""
        value = await self.get_setting("common_role_id")
        return int(value) if value else None

    # --- people and accounts ---------------------------------------------------

    async def member_id_for(self, discord_id: int) -> int | None:
        return await self._pool.fetchval("SELECT id FROM members WHERE discord_user_id = $1", discord_id)

    async def get_or_create_member(self, discord_id: int, display_name: str) -> int:
        return await self._pool.fetchval(
            """INSERT INTO members (display_name, discord_user_id) VALUES ($1, $2)
               ON CONFLICT (discord_user_id) DO UPDATE SET display_name = members.display_name
               RETURNING id""",
            display_name, discord_id,
        )

    async def get_account(self, tag: str) -> Account | None:
        row = await self._pool.fetchrow(
            """SELECT a.tag, a.name, a.member_id, a.th_level, a.ingame_clan_tag,
                      m.discord_user_id, m.display_name
               FROM accounts a LEFT JOIN members m ON m.id = a.member_id WHERE a.tag = $1""",
            normalize_tag(tag),
        )
        return _account(row) if row else None

    async def link_account(self, tag: str, member_id: int, name: str, th_level: int | None,
                           ingame_clan_tag: str | None) -> None:
        await self._pool.execute(
            """INSERT INTO accounts (tag, member_id, name, th_level, ingame_clan_tag) VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (tag) DO UPDATE SET member_id = EXCLUDED.member_id, name = EXCLUDED.name,
                   th_level = coalesce(EXCLUDED.th_level, accounts.th_level),
                   ingame_clan_tag = EXCLUDED.ingame_clan_tag, updated_at = now()""",
            normalize_tag(tag), member_id, name, th_level, ingame_clan_tag,
        )

    async def unlink_account(self, tag: str) -> None:
        await self._pool.execute(
            "UPDATE accounts SET member_id = NULL, updated_at = now() WHERE tag = $1", normalize_tag(tag))

    async def update_ingame(self, seen: dict[str, tuple[str, str, int]], tracked: set[str]) -> int:
        """Refresh name, TH and in-game clan from the clans' current member lists.
        `seen`: tag -> (clan_tag, name, th). Linked accounts that were in a
        tracked clan but aren't any more get their in-game clan cleared."""
        async with self._pool.acquire() as conn, conn.transaction():
            if seen:
                await conn.executemany(
                    """UPDATE accounts SET name = $2, th_level = $3, ingame_clan_tag = $4, updated_at = now()
                       WHERE tag = $1""",
                    [(tag, name, th, clan) for tag, (clan, name, th) in seen.items()],
                )
            gone = await conn.execute(
                """UPDATE accounts SET ingame_clan_tag = NULL, updated_at = now()
                   WHERE ingame_clan_tag = ANY($1::text[]) AND NOT (tag = ANY($2::text[]))""",
                list(tracked), list(seen),
            )
        return int(gone.split()[-1])

    # --- memberships -----------------------------------------------------------

    async def get_rank(self, member_id: int | None, clan_tag: str) -> Rank | None:
        if member_id is None:
            return None
        rank = await self._pool.fetchval(
            "SELECT rank::text FROM clan_memberships WHERE member_id = $1 AND clan_tag = $2", member_id, clan_tag)
        return DB_TO_RANK[rank] if rank else None

    async def best_rank_anywhere(self, member_id: int | None) -> Rank | None:
        if member_id is None:
            return None
        ranks = [DB_TO_RANK[r["rank"]] for r in await self._pool.fetch(
            """SELECT cm.rank::text AS rank FROM clan_memberships cm JOIN clans c ON c.tag = cm.clan_tag
               WHERE c.active AND cm.member_id = $1""", member_id)]
        return max(ranks) if ranks else None

    async def set_membership(self, member_id: int, clan_tag: str, rank: Rank, approved_by: int) -> None:
        await self._pool.execute(
            """INSERT INTO clan_memberships (member_id, clan_tag, rank, approved_by) VALUES ($1, $2, $3, $4)
               ON CONFLICT (member_id, clan_tag) DO UPDATE SET rank = EXCLUDED.rank""",
            member_id, clan_tag, RANK_TO_DB[rank], approved_by,
        )

    async def delete_membership(self, member_id: int, clan_tag: str) -> None:
        await self._pool.execute(
            "DELETE FROM clan_memberships WHERE member_id = $1 AND clan_tag = $2", member_id, clan_tag)

    async def memberships_of(self, member_id: int) -> list[Membership]:
        rows = await self._pool.fetch(
            """SELECT cm.clan_tag, cm.rank::text AS rank FROM clan_memberships cm
               JOIN clans c ON c.tag = cm.clan_tag WHERE c.active AND cm.member_id = $1""", member_id)
        return [Membership(r["clan_tag"], DB_TO_RANK[r["rank"]]) for r in rows]

    async def all_people_memberships(self) -> dict[int, list[Membership]]:
        """discord_user_id -> active memberships, for everyone with a Discord link
        (including people with no memberships, whose roles should be removed)."""
        rows = await self._pool.fetch(
            """SELECT m.discord_user_id, cm.clan_tag, cm.rank::text AS rank
               FROM members m
               LEFT JOIN clan_memberships cm ON cm.member_id = m.id
                    AND cm.clan_tag IN (SELECT tag FROM clans WHERE active)
               WHERE m.discord_user_id IS NOT NULL""")
        result: dict[int, list[Membership]] = {}
        for r in rows:
            people = result.setdefault(r["discord_user_id"], [])
            if r["clan_tag"]:
                people.append(Membership(r["clan_tag"], DB_TO_RANK[r["rank"]]))
        return result

    async def clan_people(self, clan_tag: str) -> list[ClanPerson]:
        """Everyone with a membership in the clan, with all their linked accounts."""
        rows = await self._pool.fetch(
            """SELECT m.id, m.display_name, m.discord_user_id, cm.rank::text AS rank,
                      a.tag, a.name, a.member_id, a.th_level, a.ingame_clan_tag,
                      m.discord_user_id AS owner_discord, m.display_name AS owner_name
               FROM clan_memberships cm
               JOIN members m ON m.id = cm.member_id
               LEFT JOIN accounts a ON a.member_id = m.id
               WHERE cm.clan_tag = $1
               ORDER BY cm.rank DESC, lower(m.display_name), a.th_level DESC NULLS LAST""",
            clan_tag,
        )
        people: dict[int, ClanPerson] = {}
        for r in rows:
            p = people.get(r["id"])
            if p is None:
                p = people[r["id"]] = ClanPerson(r["id"], r["display_name"], r["discord_user_id"],
                                                 DB_TO_RANK[r["rank"]])
            if r["tag"]:
                p.accounts.append(Account(r["tag"], r["name"], r["member_id"], r["owner_discord"],
                                          r["owner_name"], r["th_level"], r["ingame_clan_tag"]))
        return list(people.values())

    async def accounts_by_tag(self, tags: list[str]) -> dict[str, Account]:
        rows = await self._pool.fetch(
            """SELECT a.tag, a.name, a.member_id, a.th_level, a.ingame_clan_tag,
                      m.discord_user_id, m.display_name
               FROM accounts a LEFT JOIN members m ON m.id = a.member_id WHERE a.tag = ANY($1::text[])""",
            tags,
        )
        return {r["tag"]: _account(r) for r in rows}

    # --- audit log ---------------------------------------------------------------

    async def log_event(self, *, action: str, actor_discord_id: int, member_id: int | None = None,
                        clan_tag: str | None = None, account_tag: str | None = None,
                        from_rank: Rank | None = None, to_rank: Rank | None = None) -> None:
        await self._pool.execute(
            """INSERT INTO membership_events (member_id, clan_tag, account_tag, action, from_rank, to_rank,
                                              actor_discord_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            member_id, clan_tag, account_tag, action,
            RANK_TO_DB.get(from_rank), RANK_TO_DB.get(to_rank), actor_discord_id,
        )


def _account(r) -> Account:
    return Account(r["tag"], r["name"], r["member_id"], r["discord_user_id"], r["display_name"],
                   r["th_level"], r["ingame_clan_tag"])
