"""Integration tests against a real Postgres. Skipped unless TEST_DATABASE_URL
is set, e.g.  TEST_DATABASE_URL=postgresql://astra:astra_dev@localhost:5432/astra_test

The test database is wiped at the start of each test: never point this at
real data.
"""
import asyncio
import os

import asyncpg
import pytest

from astra.db import MIGRATIONS_DIR, run_migrations
from astra.db.donations import DonationRow, DonationsRepository
from astra.db.roster import RosterRepository

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def run_with_db(test_fn):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            await test_fn(pool)
        finally:
            await pool.close()
    asyncio.run(inner())


PLAYER_TAGS = [
    ["#2Y0VPJUVJ", "HarshMain", "Harsh"],
    ["#28VUPJRPU", "HarshAlt", "Harsh"],
    ["#QQQQQQQQ", "BobAcc", "Bob"],
    ["", "NoTag", "Nobody"],            # skipped: no tag
    ["#INVALID!", "Bad", "Bad"],        # skipped: invalid tag
]


def test_migrations_are_idempotent():
    async def t(pool):
        assert await run_migrations(pool) == []   # second run applies nothing
        assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == len(list(MIGRATIONS_DIR.glob("*.sql")))
    run_with_db(t)


def test_import_groups_accounts_by_owner_and_is_rerunnable():
    async def t(pool):
        repo = RosterRepository(pool)
        r1 = await repo.import_player_tags(PLAYER_TAGS)
        assert (r1.members_created, r1.accounts_upserted, len(r1.skipped_rows)) == (2, 3, 2)

        r2 = await repo.import_player_tags(PLAYER_TAGS)   # running again changes nothing
        assert (r2.members_created, r2.accounts_upserted) == (0, 3)
        assert await pool.fetchval("SELECT count(*) FROM members") == 2
        owners = await pool.fetch(
            "SELECT m.display_name, count(*) AS n FROM accounts a JOIN members m ON m.id = a.member_id "
            "GROUP BY 1 ORDER BY 1")
        assert [(o["display_name"], o["n"]) for o in owners] == [("Bob", 1), ("Harsh", 2)]
        assert await repo.list_account_tags() == ["#28VUPJRPU", "#2Y0VPJUVJ", "#QQQQQQQQ"]
    run_with_db(t)


def test_donation_leaderboard_sums_accounts_per_person():
    async def t(pool):
        await RosterRepository(pool).import_player_tags(PLAYER_TAGS)
        repo = DonationsRepository(pool)
        await repo.upsert_season("2026-11", [
            DonationRow("#2Y0VPJUVJ", 1000, 200),
            DonationRow("#28VUPJRPU", 500, 100),
            DonationRow("#QQQQQQQQ", 1200, 900),
        ])
        await repo.upsert_season("2026-10", [DonationRow("#QQQQQQQQ", 9999, 0)])  # other season

        board = await repo.leaderboard("2026-11")
        assert [(e.name, e.donated, e.received) for e in board] == [
            ("Harsh", 1500, 300),
            ("Bob", 1200, 900),
        ]
    run_with_db(t)


def test_donation_upsert_overwrites_instead_of_duplicating():
    async def t(pool):
        await RosterRepository(pool).import_player_tags(PLAYER_TAGS)
        repo = DonationsRepository(pool)
        await repo.upsert_season("2026-11", [DonationRow("#QQQQQQQQ", 10, 1)])
        await repo.upsert_season("2026-11", [DonationRow("#QQQQQQQQ", 50, 5)])
        assert await pool.fetchval("SELECT count(*) FROM donation_snapshots") == 1
        assert [e.donated for e in await repo.leaderboard("2026-11")] == [50]
    run_with_db(t)


def test_schema_rejects_bad_data():
    async def t(pool):
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute("INSERT INTO clans (tag, name, short_name) VALUES ('nope', 'x', 'X')")
        await RosterRepository(pool).upsert_clan("#28VUPJRPU", "Phoenix Reborn", "PR")
        with pytest.raises(asyncpg.UniqueViolationError):   # short names are unique
            await RosterRepository(pool).upsert_clan("#2Y0VPJUVJ", "Other", "PR")
    run_with_db(t)


# --- members sheet import -------------------------------------------------------
# Same layout as the real members sheet; names, IDs and tags are made up.
HEADER = ["DisplayName", "Username", "ID", "Name", "Tag", "Verified", "Clan", "Clan Tag", "Role"]
MEMBERS = [
    HEADER,
    ["Void | PR", "void", "111", "VoidMain", "#9GJL9VPVJ", "No", "Phoenix Reborn", "#28VUPJRPU", "Co"],
    ["Void | PR", "void", "111", "VoidAlt", "#G8U9P20VR", "No", "", "", ""],            # alt, in no clan
    ["Ryuk | PR", "ryuk", "222", "RyukA", "#YQPPVLQU0", "Yes", "Phoenix Reborn", "#28VUPJRPU", "Mem"],
    ["Ryuk | PR", "ryuk", "222", "RyukB", "#P80P28RY2", "Yes", "Phoenix Reborn", "#28VUPJRPU", "Co"],
    ["Fen | PR", "fen", "333", "FenDop", "#L9Q8YVVG2", "No", "DawnOfPhoenix", "#2Y0VPJUVJ", "Mem"],
    ["Suraj | KG", "suraj", "444", "Suraj", "#G2LG29QUY", "Yes", "Kingsguard", "#2J02U8JRC", "Mem"],  # untracked clan
    ["Bad", "bad", "", "NoId", "#2L2J8V8Y0", "No", "", "", ""],                        # no Discord ID
    ["Odd | PR", "odd", "555", "OddRole", "#GLLUVL8JP", "No", "Phoenix Reborn", "#28VUPJRPU", "???"],
]


def test_members_sheet_import():
    async def t(pool):
        repo = RosterRepository(pool)
        await repo.upsert_clan("#28VUPJRPU", "Phoenix Reborn", "PR")
        await repo.upsert_clan("#2Y0VPJUVJ", "DawnOfPhoenix", "DOP")

        r = await repo.import_members_sheet(MEMBERS)
        assert (r.members_created, r.accounts_upserted, r.memberships_upserted) == (5, 7, 3)
        assert any("Discord ID" in s for s in r.skipped_rows)
        assert any("unknown role" in s for s in r.skipped_rows)

        ranks = {(x["display_name"], x["clan_tag"]): x["rank"] for x in await pool.fetch(
            "SELECT m.display_name, cm.clan_tag, cm.rank::text FROM clan_memberships cm "
            "JOIN members m ON m.id = cm.member_id")}
        assert ranks == {
            ("Void", "#28VUPJRPU"): "co_leader",
            ("Ryuk", "#28VUPJRPU"): "co_leader",      # highest of Mem + Co
            ("Fen", "#2Y0VPJUVJ"): "member",
        }
        alt = await pool.fetchrow("SELECT a.member_id, a.ingame_clan_tag, m.discord_user_id FROM accounts a "
                                  "JOIN members m ON m.id = a.member_id WHERE a.tag = '#G8U9P20VR'")
        assert (alt["ingame_clan_tag"], alt["discord_user_id"]) == (None, 111)   # alt still linked
        kg = await pool.fetchrow("SELECT ingame_clan_tag, verified FROM accounts WHERE tag = '#G2LG29QUY'")
        assert (kg["ingame_clan_tag"], kg["verified"]) == ("#2J02U8JRC", True)

        again = await repo.import_members_sheet(MEMBERS)                       # re-run: no duplicates
        assert again.members_created == 0
        assert await pool.fetchval("SELECT count(*) FROM members") == 5
    run_with_db(t)


def test_members_sheet_missing_column_is_rejected():
    async def t(pool):
        with pytest.raises(ValueError, match="missing columns: Role"):
            await RosterRepository(pool).import_members_sheet([HEADER[:-1], ["x"] * 8])
    run_with_db(t)
