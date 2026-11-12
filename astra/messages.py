"""How leader actions and clan lists look in Discord. Pure formatting, no API
calls, so the output can be previewed and tested. Emojis come from
astra.emojis (configurable in emoji.json)."""
from __future__ import annotations

from typing import Iterable

import discord

from astra.branding import emoji_image_url, footer
from astra.db.membership import Account, Clan, ClanPerson
from astra.emojis import emoji, th
from astra.jobs.roster import InGameMember
from astra.leaders import RANK_NAMES, Result
from astra.roles.rules import Rank

ROLE_SHORT = {Rank.LEADER: "Lead", Rank.CO_LEADER: "Co", Rank.ELDER: "Eld", Rank.MEMBER: "Mem"}


def clan_logo(clan: Clan) -> str:
    """The clan's logo emoji from emoji.json ('logo_pr', 'logo_dop', ...), or ''."""
    return emoji(f"logo_{clan.alias.lower()}")
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
    logo = clan_logo(result.clan) if result.clan else ""
    clan = f"{logo + ' ' if logo else ''}**{result.clan.name}**" if result.clan else ""
    tag, name = result.account or ("", "")

    if result.kind in ("promoted", "demoted"):
        up = result.kind == "promoted"
        embed = discord.Embed(
            title=f"{emoji('promote' if up else 'demote')} {'Promotion' if up else 'Demotion'}",
            description="\n".join([f"{who} is now **{RANK_NAMES[result.rank]}** in {clan}.", ""] + roles + extra),
            color=GREEN if up else ORANGE)
        footer(embed, f"{'Promoted' if up else 'Demoted'} by {actor}" if actor else None)
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


def _embeds(clan: Clan, lines: list[str], intro: str = "", footer_extra: str | None = None,
            title: str | None = None) -> list[discord.Embed]:
    """List embeds with the clan's logo and name as the header, split under
    Discord's size limit, each with the standard footer."""
    chunks, body = [], intro
    for line in lines:
        if len(body) + len(line) + 1 > EMBED_LIMIT:
            chunks.append(body)
            body = ""
        body += line + "\n"
    chunks.append(body)
    embeds = []
    for n, text in enumerate(chunks[:10]):
        e = discord.Embed(title=title if n == 0 else None, description=text, color=BLACK)
        if n == 0:
            e.set_author(name=f"{clan.name} ({clan.tag})", icon_url=emoji_image_url(clan_logo(clan)))
        page = f"Page {n + 1}/{len(chunks[:10])}" if len(chunks) > 1 else None
        footer(e, " • ".join(x for x in (footer_extra, page) if x) or None)
        embeds.append(e)
    return embeds


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
    return _embeds(clan, lines, footer_extra=f"{len(members)} accounts in-game")


def clan_linked_embeds(clan: Clan, people: list[ClanPerson], in_clan: set[str]) -> list[discord.Embed]:
    """/clanlinked: a ROLE / TAG / NAME table of every registered account that's
    in the clan in-game, with the owner's mention. People with no account in the
    clan in-game are listed first, so leaders can /remove them."""
    header = "`ROLE  TAG       `  **NAME**"
    row = lambda rank, tag, name, discord_id: (
        f"`{ROLE_SHORT[rank]:<4}  {tag:<10}`  {name}" + (f"  <@{discord_id}>" if discord_id else ""))

    gone = [p for p in people if not any(a.tag in in_clan for a in p.accounts)]
    rows = sorted(((p.rank, a.tag, a.name, p.discord_user_id) for p in people for a in p.accounts
                   if a.tag in in_clan), key=lambda r: (-r[0], r[2].lower()))
    lines = []
    if gone:
        lines += [f"**{emoji('warning')} Not in {clan.alias} in-game · {len(gone)}**  ·  `/remove` if they left",
                  header]
        lines += [row(p.rank, p.accounts[0].tag if p.accounts else "—", p.display_name, p.discord_user_id)
                  for p in sorted(gone, key=lambda p: (-p.rank, p.display_name.lower()))]
        lines.append("")
    lines += [f"**{emoji('check')} In the clan · {len(rows)} accounts**", header]
    lines += [row(*r) for r in rows]
    return _embeds(clan, lines, footer_extra=f"{len(people)} registered")
