"""Leader commands end to end against a real Postgres (no Discord involved).
Skipped unless TEST_DATABASE_URL is set."""
import asyncio
import os

import asyncpg
import pytest

from astra import leaders
from astra.db import run_migrations
from astra.db.membership import MembershipRepository
from astra.jobs.roster import InGameMember
from astra.leaders import Actor, LeaderError, Target
from astra.roles.rules import PermissionDenied, Rank

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

PR, DOP = "#28VUPJRPU", "#2Y0VPJUVJ"
ADMIN = Actor(1, is_admin=True)
LEAD, CO, ELDER, MEM = (Actor(i, False) for i in (10, 20, 30, 40))
NEW = Target(99, "Newbie | PR")
TAG_NEW, TAG_ALT, TAG_ELSEWHERE = "#P2P2P2P2", "#Y8Y8Y8Y8", "#G0G0G0G0"

INGAME = {
    TAG_NEW: InGameMember(TAG_NEW, "NewbieMain", 15, PR),
    "#C0C0C0C0": InGameMember("#C0C0C0C0", "CoMain", 17, PR),
    TAG_ELSEWHERE: InGameMember(TAG_ELSEWHERE, "DopGuy", 16, DOP),
}


def run(test_fn):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            repo = MembershipRepository(pool)
            await repo.add_clan(PR, "Phoenix Reborn", "PR")
            await repo.add_clan(DOP, "DawnOfPhoenix", "DOP")
            # Existing staff of PR: a leader, a co-leader, an elder and a member.
            for actor, rank, tag in ((LEAD, Rank.LEADER, "#L0L0L0L0"), (CO, Rank.CO_LEADER, "#C0C0C0C0"),
                                     (ELDER, Rank.ELDER, "#E0E0E0E0".replace("E", "R")),
                                     (MEM, Rank.MEMBER, "#J0J0J0J0")):
                mid = await repo.get_or_create_member(actor.discord_id, f"Staff{actor.discord_id}")
                await repo.link_account(tag, mid, f"acc{actor.discord_id}", 16, PR)
                await repo.set_membership(mid, PR, rank, approved_by=1)
            await test_fn(pool, repo)
        finally:
            await pool.close()
    asyncio.run(inner())


async def rank_of(repo, discord_id, clan=PR):
    return await repo.get_rank(await repo.member_id_for(discord_id), clan)


# --- /joined ---------------------------------------------------------------------

def test_joined_by_co_leader_links_and_adds_member():
    async def t(pool, repo):
        r = await leaders.joined(repo, CO, NEW, "p2p2p2p2", "pr", INGAME)    # tag and alias are normalised
        assert await rank_of(repo, NEW.discord_id) == Rank.MEMBER
        acc = await repo.get_account(TAG_NEW)
        assert (acc.owner_discord_id, acc.name, acc.th_level, acc.owner_name) == (99, "NewbieMain", 15, "Newbie")
        assert r.sync_discord_ids == [99] and r.warnings == []
        actions = [x["action"] for x in await pool.fetch("SELECT action FROM membership_events ORDER BY id")]
        assert actions == ["linked", "joined"]
    run(t)


def test_joined_warns_when_tag_not_in_that_clan_in_game():
    async def t(pool, repo):
        r = await leaders.joined(repo, CO, NEW, TAG_ELSEWHERE, "PR", INGAME)
        assert "in DOP in-game" in r.warnings[0]
        r = await leaders.joined(repo, ADMIN, Target(98, "X"), "#Q0Q0Q0Q0", "PR", INGAME)
        assert "isn't in PR's member list yet" in r.warnings[0]
    run(t)


def test_joined_permission_and_duplicate_checks():
    async def t(pool, repo):
        with pytest.raises(PermissionDenied):
            await leaders.joined(repo, ELDER, NEW, TAG_NEW, "PR", INGAME)
        with pytest.raises(PermissionDenied):                        # co-leader of PR, not of DOP
            await leaders.joined(repo, CO, NEW, TAG_NEW, "DOP", INGAME)
        await leaders.joined(repo, CO, NEW, TAG_NEW, "PR", INGAME)
        with pytest.raises(LeaderError, match="already in PR"):
            await leaders.joined(repo, CO, NEW, TAG_NEW, "PR", INGAME)
        with pytest.raises(LeaderError, match="already linked to <@99>"):
            await leaders.joined(repo, CO, Target(77, "Other"), TAG_NEW, "PR", INGAME)
        with pytest.raises(LeaderError, match="Unknown clan"):
            await leaders.joined(repo, ADMIN, NEW, TAG_NEW, "XYZ", INGAME)
        with pytest.raises(LeaderError, match="not a valid"):
            await leaders.joined(repo, ADMIN, NEW, "hello", "PR", INGAME)
    run(t)


# --- /link -----------------------------------------------------------------------

def test_link_existing_member_with_rank():
    async def t(pool, repo):
        r = await leaders.link(repo, CO, NEW, TAG_NEW, INGAME, Rank.ELDER)
        assert await rank_of(repo, NEW.discord_id) == Rank.ELDER and "Elder" in r.message
        with pytest.raises(PermissionDenied, match="manually"):
            await leaders.link(repo, ADMIN, Target(98, "Y"), "#Q0Q0Q0Q0", {"#Q0Q0Q0Q0": InGameMember(
                "#Q0Q0Q0Q0", "Y", 16, PR)}, Rank.LEADER)
        # A co-leader can add someone at their own rank.
        await leaders.link(repo, CO, Target(98, "Y"), "#Q0Q0Q0Q0", {"#Q0Q0Q0Q0": InGameMember(
            "#Q0Q0Q0Q0", "Y", 16, PR)}, Rank.CO_LEADER)
        assert await rank_of(repo, 98) == Rank.CO_LEADER
        # Linking a second account doesn't change an existing rank.
        r = await leaders.link(repo, CO, NEW, "#C2C2C2C2", {"#C2C2C2C2": InGameMember("#C2C2C2C2", "Alt", 14, PR)})
        assert "already Elder" in r.message and await rank_of(repo, NEW.discord_id) == Rank.ELDER
    run(t)


def test_link_alt_outside_tracked_clans_links_without_membership():
    async def t(pool, repo):
        r = await leaders.link(repo, CO, NEW, TAG_ALT, INGAME)
        assert "no clan roles" in r.message
        assert (await repo.get_account(TAG_ALT)).owner_discord_id == 99
        assert await repo.memberships_of(await repo.member_id_for(99)) == []
        with pytest.raises(PermissionDenied):
            await leaders.link(repo, MEM, Target(98, "Z"), "#Q0Q0Q0Q0", INGAME)
    run(t)


def test_unlink():
    async def t(pool, repo):
        await leaders.link(repo, CO, NEW, TAG_ALT, INGAME)
        with pytest.raises(PermissionDenied):
            await leaders.unlink(repo, ELDER, TAG_ALT)
        r = await leaders.unlink(repo, CO, TAG_ALT)
        assert (await repo.get_account(TAG_ALT)).member_id is None and r.sync_discord_ids == [99]
        with pytest.raises(LeaderError, match="isn't linked"):
            await leaders.unlink(repo, CO, TAG_ALT)
    run(t)


# --- /remove ---------------------------------------------------------------------

def test_remove_rules():
    async def t(pool, repo):
        await leaders.joined(repo, CO, NEW, TAG_NEW, "PR", INGAME)
        await leaders.remove(repo, CO, NEW, "PR")
        assert await rank_of(repo, NEW.discord_id) is None
        assert (await repo.get_account(TAG_NEW)).owner_discord_id == 99      # account stays linked
        with pytest.raises(LeaderError, match="isn't in PR"):
            await leaders.remove(repo, CO, NEW, "PR")
        with pytest.raises(PermissionDenied):                                # co-leader can't remove a co-leader
            await leaders.remove(repo, CO, Target(CO.discord_id, "x"), "PR")
        with pytest.raises(PermissionDenied):
            await leaders.remove(repo, LEAD, Target(LEAD.discord_id, "x"), "PR")  # not yourself
        await leaders.remove(repo, ADMIN, Target(LEAD.discord_id, "x"), "PR")   # admins can remove a leader
    run(t)


# --- /promote, /demote ------------------------------------------------------------

def test_promote_demote_follow_in_game_rules():
    async def t(pool, repo):
        await leaders.joined(repo, CO, NEW, TAG_NEW, "PR", INGAME)
        await leaders.change_rank(repo, CO, NEW, None, +1)                    # clan inferred: only in PR
        assert await rank_of(repo, 99) == Rank.ELDER
        await leaders.change_rank(repo, CO, NEW, "PR", +1)                    # co-leaders can make co-leaders
        assert await rank_of(repo, 99) == Rank.CO_LEADER
        with pytest.raises(PermissionDenied):
            await leaders.change_rank(repo, CO, NEW, "PR", -1)                # but not demote them
        for actor in (CO, LEAD, ADMIN):                                        # nobody makes leaders via the bot
            with pytest.raises(PermissionDenied, match="manually"):
                await leaders.change_rank(repo, actor, NEW, "PR", +1)
        # A leader set by hand (or imported) can only be demoted by an admin.
        await repo.set_membership(await repo.member_id_for(99), PR, Rank.LEADER, approved_by=1)
        with pytest.raises(PermissionDenied):
            await leaders.change_rank(repo, LEAD, NEW, "PR", -1)
        with pytest.raises(LeaderError, match="already Leader"):
            await leaders.change_rank(repo, ADMIN, NEW, "PR", +1)
        await leaders.change_rank(repo, ADMIN, NEW, "PR", -1)
        assert await rank_of(repo, 99) == Rank.CO_LEADER
        with pytest.raises(PermissionDenied, match="own rank"):
            await leaders.change_rank(repo, CO, Target(CO.discord_id, "x"), "PR", -1)
    run(t)


def test_promote_needs_clan_when_in_two():
    async def t(pool, repo):
        await leaders.joined(repo, ADMIN, NEW, TAG_NEW, "PR", INGAME)
        await leaders.joined(repo, ADMIN, NEW, TAG_ELSEWHERE, "DOP", INGAME)
        with pytest.raises(LeaderError, match="in 2 clans"):
            await leaders.change_rank(repo, ADMIN, NEW, None, +1)
        with pytest.raises(LeaderError, match="already Member"):
            await leaders.change_rank(repo, ADMIN, NEW, "DOP", -1)
        with pytest.raises(LeaderError, match="isn't registered"):
            await leaders.change_rank(repo, ADMIN, Target(12345, "ghost"), "PR", +1)
    run(t)


# --- clans, roles and in-game sync --------------------------------------------------

def test_clans_aliases_and_shared_leader_role():
    async def t(pool, repo):
        await repo.set_clan_role(PR, Rank.MEMBER, 101)
        await repo.set_clan_role(PR, Rank.ELDER, 102)
        new = await repo.add_clan("#2QQQQQQQ", "Third Clan", "TC")
        await repo.set_clan_role(new.tag, Rank.MEMBER, 301)
        roles = await repo.role_map()
        assert roles[PR] == {Rank.MEMBER: 101, Rank.ELDER: 102} and roles[new.tag] == {Rank.MEMBER: 301}
        assert (await repo.find_clan("tc")).tag == "#2QQQQQQQ"
        with pytest.raises(ValueError, match="already used"):
            await repo.add_clan("#2RRRRRRR", "Fourth", "pr")
        await repo.deactivate_clan(new.tag)
        assert new.tag not in await repo.role_map() and await repo.find_clan("TC") is None
        assert 301 in await repo.all_managed_role_ids()      # still removable from people
        # A leftover Leader role row is ignored: the bot never manages the Leader role.
        await repo.set_leader_role(900)
        assert Rank.LEADER not in (await repo.role_map())[PR] and 900 not in await repo.all_managed_role_ids()
    run(t)


def test_update_ingame_marks_accounts_that_left():
    async def t(pool, repo):
        seen = {"#C0C0C0C0": (PR, "CoRenamed", 18)}            # the others aren't in PR any more
        left = await repo.update_ingame(seen, {PR, DOP})
        assert left == 3
        co = await repo.get_account("#C0C0C0C0")
        assert (co.name, co.th_level, co.ingame_clan_tag) == ("CoRenamed", 18, PR)
        assert (await repo.get_account("#L0L0L0L0")).ingame_clan_tag is None
    run(t)


def test_clan_people_groups_accounts():
    async def t(pool, repo):
        await leaders.joined(repo, CO, NEW, TAG_NEW, "PR", INGAME)
        await leaders.link(repo, CO, NEW, TAG_ALT, INGAME)
        people = {p.discord_user_id: p for p in await repo.clan_people(PR)}
        assert len(people) == 5 and people[LEAD.discord_id].rank == Rank.LEADER
        assert sorted(a.tag for a in people[99].accounts) == sorted([TAG_NEW, TAG_ALT])
        everyone = await repo.all_people_memberships()
        assert everyone[99][0].rank == Rank.MEMBER
    run(t)


def test_remove_reports_when_no_clans_are_left():
    async def t(pool, repo):
        await leaders.joined(repo, ADMIN, NEW, TAG_NEW, "PR", INGAME)
        await leaders.joined(repo, ADMIN, NEW, TAG_ELSEWHERE, "DOP", INGAME)
        r = await leaders.remove(repo, ADMIN, NEW, "PR")
        assert r.no_clans_left is False                     # still in DOP: no Ex Member yet
        r = await leaders.remove(repo, ADMIN, NEW, "DOP")
        assert r.no_clans_left is True
    run(t)


def test_family_and_status_role_settings():
    async def t(pool, repo):
        await repo.set_setting("common_role_id", "777")      # the older single common role
        assert await repo.common_roles() == {Rank.MEMBER: 777}
        await repo.set_common_role(Rank.MEMBER, 701)
        await repo.set_common_role(Rank.ELDER, 702)
        await repo.set_common_role(Rank.CO_LEADER, 703)
        assert await repo.common_roles() == {Rank.MEMBER: 701, Rank.ELDER: 702, Rank.CO_LEADER: 703}
        assert {701, 702, 703} <= await repo.all_managed_role_ids()
        await repo.set_common_role(Rank.ELDER, None)
        assert Rank.ELDER not in await repo.common_roles()
        await repo.set_status_role("ex_member", 881)
        assert (await repo.status_role("ex_member"), await repo.status_role("just_joined")) == (881, None)
    run(t)
