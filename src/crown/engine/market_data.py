"""Thin wrapper around the fetched bar DataFrames — the backtest's
MarketData adapter. A live adapter (real-time Alpaca quotes) would implement
the same `price_at`/`bar_at` shape so engine code never needs to know which
one it's talking to."""
from __future__ import annotations

from datetime import datetime

import pandas as pd


class BarReplayer:
    def __init__(self, minute_bars: dict[str, pd.DataFrame], daily_bars: dict[str, pd.DataFrame]):
        self.minute_bars = minute_bars
        self.daily_bars = daily_bars

    def price_at(self, symbol: str, ts: datetime) -> float | None:
        """Last known close at or before `ts`, same trading day only — never
        carries a stale price across a session gap."""
        df = self.minute_bars.get(symbol)
        if df is None or df.empty:
            return None
        row = df.asof(pd.Timestamp(ts))
        if row is None or (isinstance(row, pd.Series) and row.isna().all()):
            return None
        row_ts = row.name
        if row_ts is None or row_ts.date() != pd.Timestamp(ts).date():
            return None
        return float(row["close"])

    def bar_at(self, symbol: str, ts: datetime) -> pd.Series | None:
        df = self.minute_bars.get(symbol)
        if df is None or df.empty:
            return None
        row = df.asof(pd.Timestamp(ts))
        if row is None or (isinstance(row, pd.Series) and row.isna().all()):
            return None
        if row.name is None or row.name.date() != pd.Timestamp(ts).date():
            return None
        return row

    def daily_close(self, symbol: str, day) -> float | None:
        df = self.daily_bars.get(symbol)
        if df is None or df.empty:
            return None
        row = df.asof(pd.Timestamp(day))
        if row is None or (isinstance(row, pd.Series) and row.isna().all()):
            return None
        return float(row["close"])

    def all_minute_timestamps(self) -> list[pd.Timestamp]:
        ts_sets = [df.index for df in self.minute_bars.values() if not df.empty]
        if not ts_sets:
            return []
        return sorted(set().union(*ts_sets))
