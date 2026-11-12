"""/profile: a person's linked accounts, laid out like other clan bots do.

With the official API (COC_API_TOKEN) each account shows live TH and TH
weapon, hero levels and in-game role. Without it, the bot shows what it
already knows: TH from the last roster sync and the person's rank from
the clan memberships.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import discord

from astra.branding import footer
from astra.db.membership import Account, Clan
from astra.emojis import emoji, th
from astra.leaders import RANK_NAMES
from astra.roles.rules import Rank

HERO_KEYS = {                     # official API hero name -> emoji.json key
    "barbarian king": "hero_bk", "archer queen": "hero_aq", "grand warden": "hero_gw",
    "royal champion": "hero_rc", "minion prince": "hero_mp", "dragon duke": "hero_dd",
}
OFFICIAL_ROLES = {"leader": "Leader", "coLeader": "Co-Leader", "admin": "Elder", "member": "Member"}
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
EMBED_LIMIT = 4000


@dataclass
class ProfileAccount:
    tag: str
    name: str
    th: int | None
    weapon: int | None = None                  # TH weapon (giga) level
    heroes: list[tuple[str, int]] = field(default_factory=list)   # (emoji key, level)
    role_line: str | None = None               # "Co-Leader of Phoenix Reborn"
    default: bool = False


def player_link(tag: str) -> str:
    return f"https://link.clashofclans.com/en?action=OpenPlayerProfile&playerTag={quote(tag, safe='')}"


def from_official(account: Account, player: dict[str, Any]) -> ProfileAccount:
    heroes = [(HERO_KEYS[h["name"].lower()], h["level"]) for h in player.get("heroes") or []
              if h.get("village") in (None, "home") and h.get("name", "").lower() in HERO_KEYS]
    order = list(HERO_KEYS.values())
    heroes.sort(key=lambda h: order.index(h[0]))
    clan, role = player.get("clan") or {}, OFFICIAL_ROLES.get(player.get("role", ""))
    return ProfileAccount(
        tag=account.tag, name=player.get("name") or account.name, th=player.get("townHallLevel") or account.th_level,
        weapon=player.get("townHallWeaponLevel"), heroes=heroes,
        role_line=f"{role} of {clan['name']}" if clan.get("name") and role else None,
    )


def from_database(account: Account, ranks: dict[str, Rank], clans: dict[str, Clan]) -> ProfileAccount:
    clan = clans.get(account.ingame_clan_tag or "")
    rank = ranks.get(clan.tag) if clan else None
    return ProfileAccount(account.tag, account.name, account.th_level,
                          role_line=f"{RANK_NAMES[rank].replace('Co-leader', 'Co-Leader')} of {clan.name}"
                          if clan and rank is not None else None)


def mark_default(accounts: list[ProfileAccount]) -> list[ProfileAccount]:
    """Highest TH first; that one is the default (main) account."""
    accounts = sorted(accounts, key=lambda a: (-(a.th or 0), -(a.weapon or 0), a.name.lower()))
    if accounts:
        accounts[0].default = True
    return accounts


def account_block(a: ProfileAccount) -> str:
    level = f"{a.th or '?'}{str(a.weapon).translate(SUPERSCRIPT) if a.weapon else ''}"
    lines = [f"{th(a.th)} {level} • [{a.name} ({a.tag})]({player_link(a.tag)})" + (" **(Default)**" if a.default else "")]
    if a.heroes:
        lines.append(" ".join(f"{emoji(key)} {lvl}" for key, lvl in a.heroes))
    if a.role_line:
        lines.append(f"{emoji('role')} {a.role_line}")
    return "\n".join(lines)


def profile_embeds(display_name: str, discord_id: int, username: str, avatar_url: str | None,
                   accounts: list[ProfileAccount], note: str | None = None) -> list[discord.Embed]:
    head = f"**Username**\n{username}\n\n**Player Accounts ({len(accounts)})**\n"
    blocks = [account_block(a) for a in accounts] or ["No accounts linked yet. A leader can use `/link`."]
    pages, body = [], head
    for block in blocks:
        if len(body) + len(block) + 2 > EMBED_LIMIT:
            pages.append(body)
            body = ""
        body += block + "\n\n"
    if note:
        body += f"-# {note}"
    pages.append(body)
    embeds = []
    for n, text in enumerate(pages[:10]):
        e = discord.Embed(description=text, color=0x000000)
        if n == 0:
            e.set_author(name=f"{display_name} ({discord_id})", icon_url=avatar_url)
        footer(e, f"Page {n + 1}/{len(pages[:10])}" if len(pages) > 1 else None)
        embeds.append(e)
    return embeds
