"""SheetsClient.write_rows against a fake spreadsheet that enforces the real
API's grid limits, and records the order of calls."""
import asyncio
import json
import os

import gspread
import pytest

from astra.sheets import SheetsClient


class FakeWorksheet:
    def __init__(self, log, rows=1000, cols=26, title=""):
        self.log, self.row_count, self.col_count, self.title = log, rows, cols, title

    def add_rows(self, n):
        self.log.append(("add_rows", n)); self.row_count += n

    def add_cols(self, n):
        self.log.append(("add_cols", n)); self.col_count += n


class FakeSpreadsheet:
    def __init__(self, tabs):
        self.log = []
        self.tabs = {name: FakeWorksheet(self.log, *size, title=name) for name, size in tabs.items()}

    def worksheets(self):
        return list(self.tabs.values())

    def del_worksheet(self, ws):
        self.log.append(("delete", ws.title)); del self.tabs[ws.title]

    def reorder_worksheets(self, order):
        self.log.append(("order", [w.title for w in order]))

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.log.append(("add_worksheet", title)); self.tabs[title] = FakeWorksheet(self.log, rows, cols)
        return self.tabs[title]

    def values_update(self, rng, params, body):
        ws = self.tabs[rng.split("!")[0].strip("'").replace("''", "'")]
        assert len(body["values"]) <= ws.row_count, "exceeds grid limits"
        self.log.append(("update", rng, len(body["values"])))

    def values_clear(self, rng):
        self.log.append(("clear", rng))


@pytest.fixture
def client():
    # A syntactically valid but fake key; nothing here talks to Google.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    creds = json.dumps({"type": "service_account", "project_id": "p", "private_key_id": "k", "private_key": key,
                        "client_email": "a@p.iam.gserviceaccount.com", "client_id": "1",
                        "token_uri": "https://oauth2.googleapis.com/token"})
    return SheetsClient(creds, "https://docs.google.com/spreadsheets/d/x/edit", writable=True)


def write(client, ss, tab, rows):
    client._spreadsheet = ss
    asyncio.run(client.write_rows(tab, rows))
    return ss.log


ROWS = [["Name", "Donated", "Received"], ["A", 10, 1], ["B", 5, 2]]


def test_overwrites_first_then_clears_leftovers(client):
    log = write(client, FakeSpreadsheet({"Donations": (1000, 26)}), "Donations", ROWS)
    assert log[0] == ("update", "'Donations'!A1", 3)          # never cleared before writing
    assert ("clear", "'Donations'!A4:Z1000") in log            # old rows below
    assert ("clear", "'Donations'!D1:Z3") in log               # old columns to the right


def test_grows_the_grid_when_needed(client):
    log = write(client, FakeSpreadsheet({"War Stats": (2, 2)}), "War Stats", ROWS)
    assert log[:2] == [("add_rows", 1), ("add_cols", 1)]
    assert ("update", "'War Stats'!A1", 3) in log
    assert not any(e[0] == "clear" for e in log)               # exactly full: nothing left to clear


def test_creates_missing_tab(client):
    log = write(client, FakeSpreadsheet({}), "Cwl Nov2026", ROWS)
    assert log[0] == ("add_worksheet", "Cwl Nov2026")


def test_quotes_tab_names(client):
    log = write(client, FakeSpreadsheet({"Bob's tab": (10, 5)}), "Bob's tab", ROWS)
    assert log[0] == ("update", "'Bob''s tab'!A1", 3)


def test_read_only_client_asks_for_read_only_scope(client):
    from astra.sheets import READ_ONLY_SCOPES, READ_WRITE_SCOPES
    assert client._gc.http_client.auth.scopes == READ_WRITE_SCOPES


def test_keep_only_deletes_old_tabs_and_orders_the_rest(client):
    ss = FakeSpreadsheet({t: (10, 5) for t in ("Cwl Oct2026", "War Stats", "Test_Sheet", "Members", "CWL")})
    client._spreadsheet = ss
    deleted = asyncio.run(client.keep_only(["Members", "All Data", "War Stats", "CWL"]))
    assert sorted(deleted) == ["Cwl Oct2026", "Test_Sheet"]
    assert ss.log[-1] == ("order", ["Members", "War Stats", "CWL"])


def test_keep_only_refuses_to_delete_everything(client):
    client._spreadsheet = FakeSpreadsheet({"Old": (10, 5)})
    with pytest.raises(RuntimeError, match="refusing"):
        asyncio.run(client.keep_only(["Members"]))
