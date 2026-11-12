"""Pure rank and role rules for clan membership.

No Discord or database imports here on purpose: everything is plain data in,
plain data out, so it can be unit-tested without mocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Rank(IntEnum):
    """Ordered so comparisons (<, <=) express seniority."""
    MEMBER = 0
    ELDER = 1
    CO_LEADER = 2
    LEADER = 3


# Minimum rank needed to manage a clan (accept, promote, demote, remove).
MANAGER_MIN_RANK = Rank.CO_LEADER


class PermissionDenied(Exception):
    """Raised with a message that is safe to show the leader in Discord."""


def check_rank_change(
    actor_rank: Rank | None,
    target_from: Rank,
    target_to: Rank,
    *,
    actor_is_target: bool = False,
    actor_is_admin: bool = False,
) -> None:
    """Validate a promote/demote by one step. Raises PermissionDenied if not allowed.

    Rule (mirrors in-game): you can only act on people *below* you, and you can
    raise them at most to your *own* rank.
      - Co-leader: Member->Elder, Elder->Co-leader, Elder->Member.
      - Leader:    all of the above, Co-leader->Elder.
      - Nobody promotes to Leader through the bot (it's done by hand in the
        server); only server admins/owner can demote a Leader.
    Server admins (Discord 'Administrator' permission or the owner) bypass the
    rank check entirely; that is also how the first leaders get set up.
    """
    if abs(target_to - target_from) != 1:
        raise PermissionDenied("Ranks change one step at a time.")
    if target_to == Rank.LEADER:
        raise PermissionDenied("Leader is assigned manually in the server, not through the bot.")
    if actor_is_admin:
        return
    if actor_is_target:
        raise PermissionDenied("You can't change your own rank.")
    if actor_rank is None or actor_rank < MANAGER_MIN_RANK:
        raise PermissionDenied("Only co-leaders and leaders of this clan can do that.")
    if target_from >= actor_rank:
        raise PermissionDenied(
            f"You can't change the rank of a {target_from.name.replace('_', '-').title()}."
        )
    if target_to > actor_rank:
        raise PermissionDenied("You can't promote someone above your own rank.")


def check_can_remove(
    actor_rank: Rank | None,
    target_rank: Rank,
    *,
    actor_is_target: bool = False,
    actor_is_admin: bool = False,
) -> None:
    """Validate /left (removing someone from a clan). Same 'only people below you' rule."""
    if actor_is_admin:
        return
    if actor_is_target:
        raise PermissionDenied("You can't remove yourself; ask another leader.")
    if actor_rank is None or actor_rank < MANAGER_MIN_RANK:
        raise PermissionDenied("Only co-leaders and leaders of this clan can do that.")
    if target_rank >= actor_rank:
        raise PermissionDenied("You can only remove members ranked below you.")


def check_can_accept(actor_rank: Rank | None, *, actor_is_admin: bool = False) -> None:
    """Validate /joined (accepting someone into a clan as Member)."""
    if actor_is_admin:
        return
    if actor_rank is None or actor_rank < MANAGER_MIN_RANK:
        raise PermissionDenied("Only co-leaders and leaders of this clan can accept members.")


@dataclass(frozen=True)
class Membership:
    clan_tag: str
    rank: Rank


# clan_tag -> {rank -> discord_role_id}. Every clan's LEADER entry points to the
# same shared 'Leader' role id, so no special case is needed for leaders.
RoleMap = dict[str, dict[Rank, int]]


def managed_role_ids(role_map: RoleMap) -> set[int]:
    """All roles the bot is allowed to add or remove."""
    return {role_id for ranks in role_map.values() for role_id in ranks.values()}


# clan_tag -> {rank -> role id} for one clan's rank roles, and
# rank -> role id for the family-wide rank roles ("[ArIX] Member", ...).
CommonRoles = dict[Rank, int]


def desired_role_ids(memberships: list[Membership], role_map: RoleMap,
                     common_roles: CommonRoles | int | None = None) -> set[int]:
    """Roles a person should have, from all their active clan memberships.

    Clan roles stack: a PR Co-leader gets PR Member + PR Elder + PR Co-leader.
    Family roles stack the same way, by the person's highest rank in any
    tracked clan: an Elder in PR and Member in DOP gets [ArIX] Member and
    [ArIX] Elder. Union across clans for the clan roles.

    The Leader role is never managed by the bot (it's assigned by hand in the
    server), so a leader gets everything up to Co-leader here.
    """
    if isinstance(common_roles, int):          # older single "common role"
        common_roles = {Rank.MEMBER: common_roles}
    desired: set[int] = set()
    best: Rank | None = None
    for m in memberships:
        if m.clan_tag not in role_map:
            continue
        best = m.rank if best is None else max(best, m.rank)
        for rank, role_id in role_map[m.clan_tag].items():
            if rank <= m.rank and rank != Rank.LEADER:
                desired.add(role_id)
    if best is not None:
        for rank, role_id in (common_roles or {}).items():
            if rank <= best and rank != Rank.LEADER:
                desired.add(role_id)
    return desired


def role_diff(desired: set[int], current: set[int], managed: set[int]) -> tuple[set[int], set[int]]:
    """Return (to_add, to_remove). Never touches roles outside `managed`
    (e.g. Server Booster, Muted, bot roles). Idempotent: when everything is
    already in sync, both sets are empty."""
    desired = desired & managed
    to_add = desired - current
    to_remove = (current & managed) - desired
    return to_add, to_remove
