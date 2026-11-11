"""How leader actions and clan lists look in Discord. Pure formatting, no API
calls, so the output can be previewed and tested. Emojis come from
astra.emojis (configurable in emoji.json)."""
from __future__ import annotations

from typing import Iterable

import discord

from astra.db.membership import Account, Clan, ClanPerson
from astra.emojis import emoji, th
from astra.jobs.roster import InGameMember
from astra.leaders import RANK_NAMES, Result
from astra.roles.rules import Rank

RANK_EMOJI_KEYS = {Rank.LEADER: "leader", Rank.CO_LEADER: "co_leader", Rank.ELDER: "elder", Rank.MEMBER: "member"}
GREEN, ORANGE, BLACK = 0x2ECC71, 0xE67E22, 0x000000
EMBED_LIMIT = 4000   # Discord's limit is 4096; leave room


def role_lines(added: Iterable[str] = (), removed: Iterable[str] = ()) -> list[str]:
    return ([f"{emoji('role_added')} Added **{r}** role." for r in added]
            + [f"{emoji('role_removed')} Removed **{r}** role." for r in removed])


def render_result(result: Result, added: Iterable[str] = (), removed: Iterable[str] = (),
                  notes: Iterable[str] = (), actor: str = "") -> tuple[str | None, discord.Embed | None]:
    """(message text, embed) for a leader action. Promotions and demotions get
    an embed; the rest are short checklist messages."""
    roles = role_lines(added, removed)
    extra = [f"{emoji('warning')} {n}" for n in notes]
    who = f"<@{result.target_id}>" if result.target_id else "they"
    clan = f"**{result.clan.name}**" if result.clan else ""
    tag, name = result.account or ("", "")

    if result.kind in ("promoted", "demoted"):
        up = result.kind == "promoted"
        embed = discord.Embed(
            title=f"{emoji('promote' if up else 'demote')} {'Promotion' if up else 'Demotion'}",
            description="\n".join([f"{who} is now **{RANK_NAMES[result.rank]}** in {clan}.", ""] + roles + extra),
            color=GREEN if up else ORANGE)
        if actor:
            embed.set_footer(text=f"{'Promoted' if up else 'Demoted'} by {actor}")
        return None, embed

    if result.kind == "joined":
        lines = [f"{emoji('check')} Linked **{name}** (`{tag}`).", *roles, *extra,
                 f"{emoji('welcome')} Welcome to {clan}, {who}!"]
    elif result.kind == "linked":
        where = f" as **{RANK_NAMES[result.rank]}** in {clan}" if result.clan else " (not in a tracked clan)"
        lines = [f"{emoji('link')} Linked **{name}** (`{tag}`) to {who}{where}.", *roles, *extra]
    elif result.kind == "removed":
        lines = [f"{emoji('leave')} {who} has left {clan}.", *roles, *extra]
    elif result.kind == "unlinked":
        lines = [f"{emoji('unlink')} Unlinked **{name}** (`{tag}`) from {who}.", *roles, *extra]
    else:
        lines = [result.message, *roles, *extra]
    return "\n".join(lines), None


def _embeds(title: str, intro: str, lines: list[str]) -> list[discord.Embed]:
    """Leaderboard-style embeds: first one titled, all under the size limit."""
    embeds, body = [], intro
    for line in lines:
        if len(body) + len(line) + 1 > EMBED_LIMIT:
            embeds.append(discord.Embed(title=title if not embeds else None, description=body, color=BLACK))
            body = ""
        body += line + "\n"
    embeds.append(discord.Embed(title=title if not embeds else None, description=body, color=BLACK))
    return embeds[:10]


def clan_members_embeds(clan: Clan, ingame: list[InGameMember], accounts: dict[str, Account]) -> list[discord.Embed]:
    """/clanmembers: who's in the clan in-game, unlinked accounts first."""
    blank = emoji("blank")
    members = sorted(ingame, key=lambda m: (-(m.th or 0), m.name.lower()))
    owner = {m.tag: accounts[m.tag].owner_discord_id for m in members
             if m.tag in accounts and accounts[m.tag].owner_discord_id}
    unlinked = [m for m in members if m.tag not in owner]
    linked = [m for m in members if m.tag in owner]

    line = lambda m, extra="": f"{th(m.th)}{blank}`{m.tag:<10}`  {m.name}{extra}"
    lines = [f"**{emoji('cross')} Not linked · {len(unlinked)}**" + ("  ·  use `/link`" if unlinked else "")]
    lines += [line(m) for m in unlinked] or [f"{blank}Everyone is linked {emoji('welcome')}"]
    lines += ["", f"**{emoji('check')} Linked · {len(linked)}**"]
    lines += [line(m, f"  <@{owner[m.tag]}>") for m in linked]
    intro = f"**{clan.name}** · {len(members)} accounts in-game right now\n\n"
    return _embeds(f"{emoji('clan')} {clan.alias} Members", intro, lines)


def clan_linked_embeds(clan: Clan, people: list[ClanPerson], in_clan: set[str]) -> list[discord.Embed]:
    """/clanlinked: everyone registered in the clan, one line each. People with
    no account in the clan in-game come first, so leaders can /remove them."""
    blank = emoji("blank")

    def line(p: ClanPerson, icon: str) -> str:
        here = sum(1 for a in p.accounts if a.tag in in_clan)
        who = f"<@{p.discord_user_id}>" if p.discord_user_id else p.display_name
        return f"{icon}{blank}`{here:^3} {len(p.accounts):^3}`  {who}"

    gone = [p for p in people if not any(a.tag in in_clan for a in p.accounts)]
    staying = [p for p in people if p not in gone]
    lines = []
    if gone:
        lines.append(f"**{emoji('warning')} Not in {clan.alias} in-game · {len(gone)}**  ·  `/remove` if they left")
        lines += [line(p, emoji(RANK_EMOJI_KEYS[p.rank])) for p in gone]
        lines.append("")
    lines.append(f"**{emoji('check')} In the clan · {len(staying)}**")
    lines.append(f"{blank}{blank}`In  Acc`")
    lines += [line(p, emoji(RANK_EMOJI_KEYS[p.rank])) for p in staying]
    legend = "  ".join(f"{emoji(RANK_EMOJI_KEYS[r])} {RANK_NAMES[r]}" for r in sorted(Rank, reverse=True))
    lines += ["", f"-# {legend}  ·  In = accounts in {clan.alias} now  ·  Acc = accounts linked"]
    intro = f"**{clan.name}** · {len(people)} registered\n\n"
    return _embeds(f"{emoji('clan')} {clan.alias} Registered Members", intro, lines)
