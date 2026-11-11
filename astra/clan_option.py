"""The `clan` option: autocompletes from the clans in the database, so a clan
added with /clan add is available in every command straight away."""
from __future__ import annotations

import discord
from discord import app_commands

from astra.db.membership import Clan, MembershipRepository
from astra.month_option import MonthError, resolve_month


async def clan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    db = getattr(interaction.client, "db", None)
    if db is None:
        return []
    current = current.lower()
    return [app_commands.Choice(name=f"{c.alias} · {c.name}", value=c.alias)
            for c in await MembershipRepository(db).list_clans()
            if current in c.alias.lower() or current in c.name.lower()][:25]


async def resolve_filters(bot, month: str | None, clan: str | None):
    """(month start, Clan or None) for the leaderboard commands.
    For the prefix forms, a clan alias typed where the month goes also works
    (!warstats PR), so people don't have to remember the order."""
    repo = MembershipRepository(bot.db) if bot.db is not None else None
    if month and not clan and repo is not None and await repo.find_clan(month):
        month, clan = None, month
    start = resolve_month(bot.settings, month)
    if not clan:
        return start, None
    if repo is None:
        raise MonthError("Filtering by clan needs the database.")
    found = await repo.find_clan(clan)
    if found is None:
        names = ", ".join(c.alias for c in await repo.list_clans())
        raise MonthError(f"Unknown clan `{clan}`. Clans: {names}.")
    return start, found
