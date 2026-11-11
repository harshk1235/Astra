"""The `clan` option: autocompletes from the clans in the database, so a clan
added with /clan add is available in every command straight away."""
from __future__ import annotations

import discord
from discord import app_commands

from astra.db.membership import MembershipRepository


async def clan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    db = getattr(interaction.client, "db", None)
    if db is None:
        return []
    current = current.lower()
    return [app_commands.Choice(name=f"{c.alias} · {c.name}", value=c.alias)
            for c in await MembershipRepository(db).list_clans()
            if current in c.alias.lower() or current in c.name.lower()][:25]
