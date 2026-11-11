"""One shared, non-blocking Google Sheets reader for every cog.

Why:
- Each cog used to build its own credentials and gspread client.
- gspread is synchronous. Calling it directly inside a command blocks the
  bot's event loop, so while one sheet read is in flight the bot can't
  answer anything else (other commands, heartbeats, button clicks).
  asyncio.to_thread runs the call on a worker thread instead.
- Commands re-opened the spreadsheet by URL on every call (an extra HTTP
  round trip). The handle is now opened once and reused, and each read is
  a single values API call.
"""
from __future__ import annotations

import asyncio
import json

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import fill_gaps

# The bot only reads, so ask for read-only access (least privilege).
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class SheetsClient:
    def __init__(self, credentials_json: str, sheet_url: str):
        creds = Credentials.from_service_account_info(json.loads(credentials_json), scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._sheet_url = sheet_url
        self._spreadsheet: gspread.Spreadsheet | None = None

    def _open(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None:
            self._spreadsheet = self._gc.open_by_url(self._sheet_url)
        return self._spreadsheet

    def _read_sync(self, worksheet: str | None) -> list[list[str]]:
        # Quote the title so names with spaces ("War Stats") are a valid A1 range.
        # A range without a sheet name refers to the first tab.
        rng = "A:ZZ" if worksheet is None else "'" + worksheet.replace("'", "''") + "'"
        response = self._open().values_get(rng)
        # The API drops trailing empty cells; pad every row to the same width,
        # matching what Worksheet.get_all_values() returned before.
        return fill_gaps(response.get("values", []))

    async def get_values(self, worksheet: str | None = None) -> list[list[str]]:
        """All cell values of a worksheet as strings, header row included.
        With no name, reads the spreadsheet's first tab."""
        return await asyncio.to_thread(self._read_sync, worksheet)
