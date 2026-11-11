"""Apply roles in Discord from what the database says (desired state vs actual).

Commands never add or remove roles one at a time: they change the database
and then call sync_member, which works out the difference and applies it.
Running it again changes nothing, so /refreshroles can repair anything that
drifted (a failed API call, someone editing roles by hand, a rejoin).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord

from astra.roles.rules import Membership, RoleMap, desired_role_ids, role_diff

log = logging.getLogger(__name__)


class RoleSyncError(Exception):
    """Safe to show in Discord."""


@dataclass(frozen=True)
class SyncOutcome:
    found: bool           # False if the user isn't in the server
    added: int = 0
    removed: int = 0


async def get_member(guild: discord.Guild, discord_id: int) -> discord.Member | None:
    member = guild.get_member(discord_id)
    if member is None:
        try:
            member = await guild.fetch_member(discord_id)
        except discord.NotFound:
            return None
    return member


async def sync_member(guild: discord.Guild, discord_id: int, memberships: list[Membership],
                      role_map: RoleMap, managed: set[int], *, reason: str = "Astra role sync") -> SyncOutcome:
    member = await get_member(guild, discord_id)
    if member is None:
        return SyncOutcome(found=False)
    desired = desired_role_ids(memberships, role_map)
    to_add, to_remove = role_diff(desired, {r.id for r in member.roles}, managed)
    add = [r for r in (guild.get_role(i) for i in to_add) if r is not None]       # skip deleted roles
    remove = [r for r in (guild.get_role(i) for i in to_remove) if r is not None]
    try:
        if add:
            await member.add_roles(*add, reason=reason)
        if remove:
            await member.remove_roles(*remove, reason=reason)
    except discord.Forbidden:
        raise RoleSyncError(
            "I'm not allowed to change some of these roles. In Server Settings → Roles, drag my role "
            "above every clan role, and make sure I have the Manage Roles permission."
        )
    return SyncOutcome(True, len(add), len(remove))
