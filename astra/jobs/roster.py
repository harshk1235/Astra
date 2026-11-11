"""Who is in each tracked clan right now, in-game (from ClashKing).

GET /v2/clan/{tag}/cached returns the clan profile with its member list
(tag, name, TH; ClashKing doesn't include in-game roles). Used by /link,
/joined, /clanmembers and /clanlinked, and hourly to keep linked accounts'
names, TH and in-game clan current.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from astra.db.membership import MembershipRepository
from astra.jobs.donations import JsonClient
from astra.tags import normalize_tag, url_tag


@dataclass(frozen=True)
class InGameMember:
    tag: str
    name: str
    th: int | None
    clan_tag: str


async def fetch_clan_members(client: JsonClient, clan_tag: str) -> list[InGameMember]:
    data = await client.get_json(f"/v2/clan/{url_tag(clan_tag)}/cached") or {}
    return [InGameMember(normalize_tag(m["tag"]), m.get("name") or m["tag"], m.get("townHallLevel"), clan_tag)
            for m in data.get("members") or []]


async def fetch_ingame(client: JsonClient, clan_tags: list[str]) -> dict[str, InGameMember]:
    """tag -> where that account is right now, across all the given clans."""
    lists = await asyncio.gather(*(fetch_clan_members(client, t) for t in clan_tags))
    return {m.tag: m for members in lists for m in members}


async def sync_roster(client: JsonClient, repo: MembershipRepository) -> tuple[int, int]:
    """Update linked accounts from the in-game member lists.
    Returns (accounts seen in a tracked clan, accounts that left one)."""
    clans = [c.tag for c in await repo.list_clans()]
    if not clans:
        return 0, 0
    ingame = await fetch_ingame(client, clans)
    left = await repo.update_ingame({t: (m.clan_tag, m.name, m.th) for t, m in ingame.items()}, set(clans))
    return len(ingame), left
