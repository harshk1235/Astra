"""CSV files for Discord: the /export command and the "Download CSV" buttons."""
from __future__ import annotations

import csv
import io

import discord


def to_csv_file(rows: list[list], filename: str) -> discord.File:
    """UTF-8 with a BOM, so Excel opens player names with emoji/CJK correctly."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return discord.File(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename=filename)


def safe_filename(*parts: str) -> str:
    keep = "-".join(p for p in parts if p)
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in keep).strip("-").lower() + ".csv"


class CsvButton(discord.ui.Button):
    """A 'Download CSV' button; the file goes only to whoever clicks it."""

    def __init__(self, rows: list[list], filename: str):
        super().__init__(label="Download CSV", emoji="📥", style=discord.ButtonStyle.secondary)
        self.rows, self.filename = rows, filename

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(file=to_csv_file(self.rows, self.filename), ephemeral=True)


class CsvView(discord.ui.View):
    def __init__(self, rows: list[list], filename: str, timeout: float = 900):
        super().__init__(timeout=timeout)
        self.add_item(CsvButton(rows, filename))
