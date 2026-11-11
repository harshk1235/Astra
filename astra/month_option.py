"""The `month` option shared by /donos, /warstats and /cwlstats."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands

from astra.months import available_months, month_key, month_label, parse_month


class MonthError(ValueError):
    """Message is safe to show in Discord."""


def _window(settings, now: datetime | None = None) -> list[datetime]:
    return available_months(now or datetime.now(timezone.utc),
                            settings.data_retention_months, settings.data_start_month)


def resolve_month(settings, value: str | None, now: datetime | None = None) -> datetime:
    """None -> current month. Otherwise 'YYYY-MM' (or 'October 2026') inside the
    retention window; anything else raises MonthError."""
    months = _window(settings, now)
    if not value:
        return months[0]
    value = value.strip()
    for m in months:
        if value in (month_key(m), month_label(m)) or value.lower() == month_label(m).lower():
            return m
    try:
        parse_month(value)
    except ValueError:
        raise MonthError(f"`{value}` isn't a month. Use the format YYYY-MM, e.g. `{month_key(months[0])}`.")
    raise MonthError("Data is only kept for: " + ", ".join(month_label(m) for m in months) + ".")


async def month_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    months = _window(interaction.client.settings)
    current = current.lower()
    return [app_commands.Choice(name=month_label(m), value=month_key(m))
            for m in months if current in month_label(m).lower() or current in month_key(m)][:25]
