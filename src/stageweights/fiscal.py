"""Fiscal calendar: 13-week quarters that start on Monday 2 January 2023."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

FISCAL_START = date(2023, 1, 2)
QUARTER_DAYS = 91
N_QUARTERS = 12
TICKS_PER_QUARTER = QUARTER_DAYS // 7


def quarter_start(index: int) -> date:
    """First day (a Monday) of the fiscal quarter with the given zero-based index."""
    return FISCAL_START + timedelta(days=QUARTER_DAYS * index)


def quarter_end(index: int) -> date:
    """First day of the next quarter, i.e. the exclusive end of this one."""
    return quarter_start(index + 1)


def quarter_label(index: int) -> str:
    """Label such as ``FY2024 Q1``; index 0 is FY2023 Q1."""
    return f"FY{2023 + index // 4} Q{index % 4 + 1}"


def utc_midnight(day: date) -> datetime:
    """The UTC instant at which a snapshot dated ``day`` is taken."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)
