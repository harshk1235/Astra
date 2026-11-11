"""Months ("seasons") for leaderboards, and the data retention window.

Everything is a calendar month in UTC, identified by 'YYYY-MM'.
The bot keeps the current month plus DATA_RETENTION_MONTHS before it, and
never anything before DATA_START_MONTH.
"""
from __future__ import annotations

from datetime import datetime, timezone


def month_start(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def add_months(start: datetime, n: int) -> datetime:
    """Month start `n` months after (or before, if negative) `start`'s month."""
    index = start.year * 12 + (start.month - 1) + n
    return month_start(start).replace(year=index // 12, month=index % 12 + 1)


def next_month_start(dt: datetime) -> datetime:
    return add_months(month_start(dt), 1)


def month_key(dt: datetime) -> str:
    return month_start(dt).strftime("%Y-%m")


def parse_month(value: str) -> datetime:
    """'2026-10' -> 2026-10-01 00:00 UTC. Raises ValueError for anything else."""
    return datetime.strptime(value.strip(), "%Y-%m").replace(tzinfo=timezone.utc)


def month_label(dt: datetime) -> str:
    return month_start(dt).strftime("%B %Y")          # 'October 2026'


def cwl_sheet_name(dt: datetime) -> str:
    """Tab name the old CWL script used: 'Cwl Oct2026', but 'Cwl June2026' / 'Cwl July2026'."""
    full = month_start(dt).strftime("%B")
    return f"Cwl {full if full in ('June', 'July') else full[:3]}{dt.year}"


def retention_cutoff(now: datetime, retention_months: int, start_month: str) -> datetime:
    """Oldest month start the bot keeps: current month minus `retention_months`,
    but never before `start_month`."""
    return max(add_months(month_start(now), -retention_months), parse_month(start_month))


def available_months(now: datetime, retention_months: int, start_month: str) -> list[datetime]:
    """Months a command can show, newest first."""
    months, m = [], month_start(now)
    cutoff = retention_cutoff(now, retention_months, start_month)
    while m >= cutoff:
        months.append(m)
        m = add_months(m, -1)
    return months
