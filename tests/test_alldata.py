import asyncio
import csv
import io
import os
from datetime import datetime, timezone

import asyncpg
import pytest

from astra.alldata import ALL_DATA_HEADER, all_data_rows, members_rows
from astra.csv_export import safe_filename, to_csv_file
from astra.db import run_migrations
from astra.db.donations import DonationRow, DonationsRepository
from astra.db.membership import MembershipRepository
from astra.db.wars import AttackRecord, WarRecord, WarsRepository
from astra.roles.rules import Rank

UTC = timezone.utc
PR, DOP = "#28VUPJRPU", "#2Y0VPJUVJ"
NOV = datetime(2026, 11, 1, tzinfo=UTC)
DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def test_csv_has_bom_and_rows():
    f = to_csv_file([["Name", "Donated"], ["真白", 5]], "x.csv")
    raw = f.fp.read()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert list(csv.reader(io.StringIO(raw.decode("utf-8-sig")))) == [["Name", "Donated"], ["真白", "5"]]
    assert safe_filename("astra", "PR", "2026-11") == "astra-pr-2026-11.csv"


def with_db(test_fn):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            repo = MembershipRepository(pool)
            await repo.add_clan(PR, "Phoenix Reborn", "PR")
            await repo.add_clan(DOP, "DawnOfPhoenix", "DOP")
            harsh = await repo.get_or_create_member(42, "Harsh")
            bob = await repo.get_or_create_member(43, "Bob")
            await repo.link_account("#PYLQPYLQ", harsh, "Main", 17, PR)
            await repo.link_account("#PYLQPYL2", harsh, "Alt", 14, None)
            await repo.link_account("#CCCCCCCC", bob, "BobAcc", 16, DOP)
            await repo.set_membership(harsh, PR, Rank.CO_LEADER, 1)
            await test_fn(pool)
        finally:
            await pool.close()
    asyncio.run(inner())


@needs_db
def test_all_data_one_row_per_account():
    async def t(pool):
        await DonationsRepository(pool).upsert_season("2026-11", [
            DonationRow("#PYLQPYLQ", PR, 500, 50), DonationRow("#CCCCCCCC", DOP, 300, 30)])
        wars = WarsRepository(pool)
        s = datetime(2026, 11, 10, 20, tzinfo=UTC)
        await wars.upsert_war(WarRecord(PR, "regular", None, "#2JLQ98UCR", "Opp", 15, "warEnded", s,
                                        s.replace(day=11), s.replace(day=12)),
                              [AttackRecord(1, "#PYLQPYLQ", None, 17, "#QQQQQQQQ", 17, 3, 100.0, 90)])
        rows = await all_data_rows(pool, NOV)
        assert rows[0] == ALL_DATA_HEADER
        by_tag = {r[5]: dict(zip(ALL_DATA_HEADER, r)) for r in rows[1:]}
        # Harsh's main and alt (he's a member), Bob because he donated.
        assert set(by_tag) == {"#PYLQPYLQ", "#PYLQPYL2", "#CCCCCCCC"}
        main = by_tag["#PYLQPYLQ"]
        assert (main["Owner"], main["Discord ID"], main["Ranks"], main["In-game Clan"]) == \
               ("Harsh", "42", "PR Co-Leader", "Phoenix Reborn")
        assert (main["Donated"], main["War Attacks"], main["War Triples"], main["War Hit Rate %"]) == (500, 1, 1, 100)
        # Clan filter: only DOP numbers, only people in DOP or active in DOP.
        dop = await all_data_rows(pool, NOV, DOP)
        assert [r[5] for r in dop[1:]] == ["#CCCCCCCC"]
    with_db(t)


@needs_db
def test_members_rows():
    async def t(pool):
        rows = await members_rows(pool)
        assert rows[0][:3] == ["Owner", "Discord ID", "Ranks"]
        assert [r[3] for r in rows[1:]] == ["BobAcc", "Main", "Alt"]
    with_db(t)
