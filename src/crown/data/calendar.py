"""NYSE trading-day / market-hours calendar. All timestamps returned are
timezone-aware UTC — 07-scheduling.md is explicit that a wrong timezone here
means missing the entry window, so we never hand back naive local times."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")


def trading_days(start: str | date, end: str | date) -> list[date]:
    sched = _NYSE.schedule(start_date=str(start), end_date=str(end))
    return [ts.date() for ts in sched.index]


def is_trading_day(day: str | date) -> bool:
    return len(trading_days(day, day)) == 1


def market_open_close(day: str | date) -> tuple[datetime, datetime] | None:
    """Returns (open_utc, close_utc) for a trading day, or None if the market
    is closed that day (weekend/holiday)."""
    sched = _NYSE.schedule(start_date=str(day), end_date=str(day))
    if sched.empty:
        return None
    row = sched.iloc[0]
    return row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime()


def next_trading_day(day: str | date) -> date:
    d = pd.Timestamp(day)
    sched = _NYSE.schedule(start_date=d, end_date=d + pd.Timedelta(days=14))
    for ts in sched.index:
        if ts.date() > d.date():
            return ts.date()
    raise RuntimeError(f"no trading day found within 14 days after {day}")
