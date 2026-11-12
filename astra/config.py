"""All configuration comes from environment variables (or a local .env file).

Reading env vars in one place means a missing value fails fast at startup
with a clear message, instead of a KeyError deep inside a command.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_id: int
    google_credentials_json: str
    google_sheet_url: str
    # Optional while cogs still read Sheets; required once they read Postgres.
    database_url: str | None = None
    clashking_base_url: str = "https://api.clashk.ing"
    # Write leaderboards back to GOOGLE_SHEET_URL after each job (needs Editor access).
    sheets_export: bool = True
    # Keep the current month plus this many months before it...
    data_retention_months: int = 2
    # ...but never anything before this month (YYYY-MM).
    data_start_month: str = "2026-10"
    # Optional channel where every leader action (joined, promoted, ...) is posted.
    log_channel_id: int | None = None
    # Footer icon for every embed; empty = the bot's own avatar (recommended).
    footer_icon_url: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()  # no-op if there is no .env file; real env vars win

        sheet_url = os.environ.get("GOOGLE_SHEET_URL", "").strip()
        if not sheet_url and os.environ.get("SHEET_URL"):
            # Old name used by cwlstats.py; accepted so existing deployments keep working.
            log.warning("SHEET_URL is deprecated; rename it to GOOGLE_SHEET_URL")
            sheet_url = os.environ["SHEET_URL"].strip()
        if not sheet_url:
            raise ConfigError("Missing required environment variable: GOOGLE_SHEET_URL (see .env.example)")

        guild_id = _require("GUILD_ID")
        if not guild_id.isdigit():
            raise ConfigError("GUILD_ID must be a numeric Discord server ID")

        return cls(
            discord_token=_require("TOKEN"),
            guild_id=int(guild_id),
            google_credentials_json=_require("CREDENTIALS_JSON"),
            google_sheet_url=sheet_url,
            database_url=os.environ.get("DATABASE_URL", "").strip() or None,
            clashking_base_url=os.environ.get("CLASHKING_BASE_URL", "").strip() or "https://api.clashk.ing",
            sheets_export=os.environ.get("SHEETS_EXPORT", "true").strip().lower() not in ("0", "false", "no", "off"),
            data_retention_months=int(os.environ.get("DATA_RETENTION_MONTHS", "2")),
            data_start_month=os.environ.get("DATA_START_MONTH", "").strip() or "2026-10",
            log_channel_id=int(os.environ["LOG_CHANNEL_ID"]) if os.environ.get("LOG_CHANNEL_ID", "").strip().isdigit() else None,
            footer_icon_url=os.environ.get("FOOTER_ICON_URL", "").strip() or None,
        )
