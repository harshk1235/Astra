"""One-off setup commands for the database.

    python -m scripts.seed migrate
    python -m scripts.seed add-clan "#28VUPJRPU" "Phoenix Reborn" PR
    python -m scripts.seed import-player-tags
    python -m scripts.seed import-members "<members sheet URL>" [--tab "Sheet1"]

With Docker:  docker compose run --rm bot python -m scripts.seed <command> ...
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from astra.config import ConfigError, Settings
from astra.db import create_pool, run_migrations
from astra.db.roster import RosterRepository
from astra.sheets import SheetsClient
from astra.tags import normalize_tag


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.seed")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply pending migrations")
    add = sub.add_parser("add-clan", help="add or rename a tracked clan")
    add.add_argument("tag")
    add.add_argument("name")
    add.add_argument("short_name", help="short label used in role names, e.g. PR")
    sub.add_parser("import-player-tags", help="copy the 'Player Tags' sheet into Postgres")
    imp = sub.add_parser("import-members", help="import the members sheet (Discord IDs, accounts, clans, roles)")
    imp.add_argument("url", help="members spreadsheet URL (shared with the service account)")
    imp.add_argument("--tab", default=None, help="tab name; defaults to the first tab")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if not settings.database_url:
        raise ConfigError("DATABASE_URL is not set")
    pool = await create_pool(settings.database_url)
    try:
        applied = await run_migrations(pool)
        if args.cmd == "migrate":
            print(f"Applied: {applied or 'nothing (up to date)'}")
        elif args.cmd == "add-clan":
            await RosterRepository(pool).upsert_clan(args.tag, args.name, args.short_name)
            print(f"Saved clan {args.short_name} ({normalize_tag(args.tag)})")
        elif args.cmd == "import-player-tags":
            sheets = SheetsClient(settings.google_credentials_json, settings.google_sheet_url)
            rows = (await sheets.get_values("Player Tags"))[1:]
            result = await RosterRepository(pool).import_player_tags(rows)
            print(f"Members created: {result.members_created}, accounts upserted: {result.accounts_upserted}")
            for reason in result.skipped_rows:
                print(f"  skipped {reason}")
        elif args.cmd == "import-members":
            sheets = SheetsClient(settings.google_credentials_json, args.url)
            rows = await sheets.get_values(args.tab)
            result = await RosterRepository(pool).import_members_sheet(rows)
            print(f"People created: {result.members_created}, accounts linked: {result.accounts_upserted}, "
                  f"clan memberships: {result.memberships_upserted}")
            for reason in result.skipped_rows:
                print(f"  note: {reason}")
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
