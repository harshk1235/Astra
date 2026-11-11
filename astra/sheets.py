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
- write_rows replaces a tab's contents without ever leaving it empty, so a
  reader (the bot, a leader) never catches it half-written.
"""
from __future__ import annotations

import asyncio
import json

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import fill_gaps, rowcol_to_a1

READ_ONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
READ_WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, credentials_json: str, sheet_url: str, *, writable: bool = False):
        # Least privilege: only ask for write access when this client exports.
        scopes = READ_WRITE_SCOPES if writable else READ_ONLY_SCOPES
        creds = Credentials.from_service_account_info(json.loads(credentials_json), scopes=scopes)
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

    def _write_sync(self, worksheet: str, rows: list[list]) -> None:
        ss = self._open()
        try:
            ws = ss.worksheet(worksheet)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=worksheet, rows=max(len(rows) + 20, 100),
                                  cols=max(max((len(r) for r in rows), default=1), 10))
        width = max((len(r) for r in rows), default=1)
        # values.update fails if the range is bigger than the sheet's grid.
        if ws.row_count < len(rows):
            ws.add_rows(len(rows) - ws.row_count)
        if ws.col_count < width:
            ws.add_cols(width - ws.col_count)
        title = "'" + worksheet.replace("'", "''") + "'"
        # 1) Overwrite in place (never clear first, so the tab is never empty).
        ss.values_update(f"{title}!A1", params={"valueInputOption": "RAW"}, body={"values": rows})
        # 2) Clear whatever is left below and to the right of the new data.
        last_col = rowcol_to_a1(1, ws.col_count).rstrip("1")
        if len(rows) < ws.row_count:
            ss.values_clear(f"{title}!A{len(rows) + 1}:{last_col}{ws.row_count}")
        if width < ws.col_count and rows:
            ss.values_clear(f"{title}!{rowcol_to_a1(1, width + 1)}:{last_col}{len(rows)}")

    async def write_rows(self, worksheet: str, rows: list[list]) -> None:
        """Replace a tab's contents with `rows` (header included). Creates the
        tab if needed. Values are written as-is (RAW), not parsed as formulas."""
        await asyncio.to_thread(self._write_sync, worksheet, rows)

    async def get_values(self, worksheet: str | None = None) -> list[list[str]]:
        """All cell values of a worksheet as strings, header row included.
        With no name, reads the spreadsheet's first tab."""
        return await asyncio.to_thread(self._read_sync, worksheet)
