"""sync_member against a fake Discord guild."""
import asyncio
from types import SimpleNamespace

import discord
import pytest

from astra.roles.rules import Membership, Rank
from astra.roles.sync import RoleSyncError, sync_member

PR = "#28VUPJRPU"
ROLE_MAP = {PR: {Rank.MEMBER: 101, Rank.ELDER: 102, Rank.CO_LEADER: 103, Rank.LEADER: 900}}
MANAGED = {101, 102, 103, 900}
BOOSTER = 555


class FakeMember:
    def __init__(self, role_ids, forbidden=False):
        self.roles = [SimpleNamespace(id=i) for i in role_ids]
        self.forbidden, self.calls = forbidden, []

    async def add_roles(self, *roles, reason=None):
        if self.forbidden:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        self.calls.append(("add", sorted(r.id for r in roles)))

    async def remove_roles(self, *roles, reason=None):
        self.calls.append(("remove", sorted(r.id for r in roles)))


COMMON = 777


class FakeGuild:
    def __init__(self, members, existing_roles=MANAGED | {BOOSTER, COMMON}):
        self.members, self.existing = members, existing_roles

    def get_member(self, i):
        return self.members.get(i)

    async def fetch_member(self, i):
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")

    def get_role(self, i):
        return SimpleNamespace(id=i, name=f"role{i}") if i in self.existing else None


def run(coro):
    return asyncio.run(coro)


def test_promotion_adds_only_the_new_rank_role():
    m = FakeMember({101, BOOSTER})
    out = run(sync_member(FakeGuild({7: m}), 7, [Membership(PR, Rank.ELDER)], ROLE_MAP, MANAGED))
    assert m.calls == [("add", [102])] and (out.added, out.removed) == (1, 0)


def test_removal_takes_clan_roles_but_not_others():
    m = FakeMember({101, 102, BOOSTER})
    run(sync_member(FakeGuild({7: m}), 7, [], ROLE_MAP, MANAGED))
    assert m.calls == [("remove", [101, 102])]


def test_in_sync_member_makes_no_api_calls():
    m = FakeMember({101, 102, 103, 900})     # 900 = Leader role: assigned by hand, left alone
    out = run(sync_member(FakeGuild({7: m}), 7, [Membership(PR, Rank.LEADER)], ROLE_MAP, MANAGED))
    assert m.calls == [] and (out.added, out.removed) == (0, 0)


def test_user_not_in_server():
    assert run(sync_member(FakeGuild({}), 7, [Membership(PR, Rank.MEMBER)], ROLE_MAP, MANAGED)).found is False


def test_deleted_role_is_skipped():
    m = FakeMember(set())
    run(sync_member(FakeGuild({7: m}, existing_roles={101}), 7, [Membership(PR, Rank.ELDER)], ROLE_MAP, MANAGED))
    assert m.calls == [("add", [101])]                     # role 102 no longer exists in the server


def test_role_hierarchy_problem_gives_clear_message():
    with pytest.raises(RoleSyncError, match="drag my role"):
        run(sync_member(FakeGuild({7: FakeMember(set(), forbidden=True)}), 7,
                        [Membership(PR, Rank.MEMBER)], ROLE_MAP, MANAGED))


def test_family_role_given_with_first_clan_and_removed_with_last():
    managed = MANAGED | {COMMON}
    m = FakeMember({BOOSTER})
    out = run(sync_member(FakeGuild({7: m}), 7, [Membership(PR, Rank.MEMBER)], ROLE_MAP, managed,
                          common_roles={Rank.MEMBER: COMMON}))
    assert m.calls == [("add", [101, COMMON])]
    assert sorted(out.added_roles) == ["role101", "role777"]
    m = FakeMember({101, COMMON, BOOSTER})
    run(sync_member(FakeGuild({7: m}), 7, [], ROLE_MAP, managed, common_roles={Rank.MEMBER: COMMON}))
    assert m.calls == [("remove", [101, COMMON])]


def test_status_roles_change_only_what_is_needed():
    from astra.roles.sync import change_status_roles
    EX, JUST = 881, 882
    m = FakeMember({EX, BOOSTER})
    guild = FakeGuild({7: m}, existing_roles={EX, JUST, BOOSTER})
    added, removed = run(change_status_roles(guild, 7, remove=[EX, JUST], reason="t"))
    assert (added, removed) == ([], ["role881"]) and m.calls == [("remove", [EX])]
    m = FakeMember(set())
    added, _ = run(change_status_roles(FakeGuild({7: m}, existing_roles={EX}), 7, add=[EX, None], reason="t"))
    assert added == ["role881"]
