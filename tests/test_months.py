from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from astra.month_option import MonthError, resolve_month
from astra.months import (add_months, available_months, cwl_sheet_name, month_key, month_label,
                          next_month_start, retention_cutoff)

UTC = timezone.utc
d = lambda y, m, day=15: datetime(y, m, day, 12, tzinfo=UTC)


def test_add_months_crosses_years():
    assert add_months(d(2026, 11), -2) == datetime(2026, 9, 1, tzinfo=UTC)
    assert add_months(d(2026, 1), -1) == datetime(2025, 12, 1, tzinfo=UTC)
    assert add_months(d(2026, 12), 1) == datetime(2027, 1, 1, tzinfo=UTC)
    assert next_month_start(d(2026, 12)) == datetime(2027, 1, 1, tzinfo=UTC)


def test_retention_window_starts_at_october_2026():
    # Current month plus 2 before it, never before 2026-10.
    assert retention_cutoff(d(2026, 11), 2, "2026-10") == datetime(2026, 10, 1, tzinfo=UTC)
    assert retention_cutoff(d(2026, 12), 2, "2026-10") == datetime(2026, 10, 1, tzinfo=UTC)
    assert retention_cutoff(d(2027, 1), 2, "2026-10") == datetime(2026, 11, 1, tzinfo=UTC)
    assert [month_key(m) for m in available_months(d(2026, 11), 2, "2026-10")] == ["2026-11", "2026-10"]
    assert [month_key(m) for m in available_months(d(2027, 2), 2, "2026-10")] == ["2027-02", "2027-01", "2026-12"]


def test_cwl_sheet_name_matches_old_script():
    assert cwl_sheet_name(d(2026, 11)) == "Cwl Nov2026"
    assert cwl_sheet_name(d(2026, 6)) == "Cwl June2026"
    assert cwl_sheet_name(d(2026, 7)) == "Cwl July2026"
    assert month_label(d(2026, 10)) == "October 2026"


@dataclass
class S:
    data_retention_months: int = 2
    data_start_month: str = "2026-10"


def test_resolve_month():
    now = d(2026, 11)
    assert resolve_month(S(), None, now) == datetime(2026, 11, 1, tzinfo=UTC)
    assert resolve_month(S(), "2026-10", now) == datetime(2026, 10, 1, tzinfo=UTC)
    assert resolve_month(S(), "october 2026", now) == datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(MonthError, match="only kept for: November 2026, October 2026"):
        resolve_month(S(), "2026-09", now)
    with pytest.raises(MonthError, match="isn't a month"):
        resolve_month(S(), "last month", now)
