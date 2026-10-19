"""End-to-end wiring test for the event loop: letter activation -> entry
trigger -> approval latency -> guardrail check -> fill -> stop-out. Uses
synthetic bars injected directly, so it needs no Alpaca/Anthropic API keys
and no network access — it proves the runner's plumbing is correct, while
the pure logic it calls (zonecheck, fills, guardrails) is already covered
by their own dedicated unit tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from crown.backtest.runner import BacktestRunner
from crown.data.calendar import market_open_close, trading_days
from crown.models import (
    EntryTrigger,
    ParsedLetter,
    Stop as LetterStop,
    TacticalSummaryRow,
)


def make_minute_bars(symbol: str, days: list, price_by_day: dict) -> pd.DataFrame:
    """5-minute bars across market hours for each day, linearly interpolating
    from price_by_day[day]['open'] to price_by_day[day]['close']."""
    rows = []
    for day in days:
        oc = market_open_close(day)
        if not oc:
            continue
        open_p = price_by_day[day]["open"]
        close_p = price_by_day[day]["close"]
        start, end = oc
        n_steps = int((end - start).total_seconds() // 300)
        for i in range(n_steps + 1):
            ts = start + timedelta(minutes=5 * i)
            frac = i / n_steps if n_steps else 0
            px = open_p + (close_p - open_p) * frac
            rows.append({"timestamp": ts, "open": px, "high": px + 0.05, "low": px - 0.05, "close": px, "volume": 1000})
    df = pd.DataFrame(rows).set_index("timestamp")
    return df


def make_daily_bars(minute_df: pd.DataFrame) -> pd.DataFrame:
    daily = minute_df.groupby(minute_df.index.date).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    )
    daily.index = pd.to_datetime(daily.index, utc=True)
    daily.index.name = "timestamp"
    return daily


def make_letter(letter_date: str, rows: list[TacticalSummaryRow]) -> ParsedLetter:
    return ParsedLetter(letter_date=letter_date, is_mid_cycle=False, tactical_summary=rows)


def test_entered_position_fills_then_stops_out(cfg):
    days = trading_days("2026-06-01", "2026-06-10")
    price_by_day = {
        days[0]: {"open": 100.0, "close": 100.0},   # letter day: stage & fill near 100
        days[1]: {"open": 100.0, "close": 98.0},
        days[2]: {"open": 98.0, "close": 93.0},      # crosses the 95 stop
    }
    for d in days[3:]:
        price_by_day[d] = {"open": 93.0, "close": 93.0}

    bars = make_minute_bars("TEST", days, price_by_day)
    daily = make_daily_bars(bars)

    row = TacticalSummaryRow(
        market="TEST long", trade_type="long_equity", view="bullish",
        entry_trigger=EntryTrigger(type="entered", level=100.0, condition="entered ~100", triggered=True),
        stop=LetterStop(type="price", value=95.0),
        targets=[], sizing_multiplier=1.0,
    )
    letter = make_letter(days[0].isoformat(), [row])

    runner = BacktestRunner(cfg)
    result = runner.run(
        start=days[0], end=days[-1], letters=[letter],
        minute_bars={"TEST": bars}, daily_bars={"TEST": daily},
    )

    assert len(result.runtimes) == 1
    rt = next(iter(result.runtimes.values()))
    assert rt.status == "closed"
    assert rt.position is not None
    assert rt.position.realized_pnl < 0  # stopped out below entry
    assert rt.position.close_reason == "stop_hit"

    fill_events = [f for f in result.fills if f["trade_id"] == rt.trade.trade_id]
    assert any(f["side"] == "buy" for f in fill_events)
    assert any(f["side"] == "close" for f in fill_events)


def test_no_chase_skip_when_price_already_ran_away(cfg):
    days = trading_days("2026-06-01", "2026-06-03")
    price_by_day = {d: {"open": 150.0, "close": 150.0} for d in days}  # far from the 100 trigger

    bars = make_minute_bars("TEST", days, price_by_day)
    daily = make_daily_bars(bars)

    row = TacticalSummaryRow(
        market="TEST long", trade_type="long_equity", view="bullish",
        entry_trigger=EntryTrigger(type="entered", level=100.0, condition="entered ~100", triggered=True),
        stop=LetterStop(type="price", value=95.0),
        targets=[], sizing_multiplier=1.0,
    )
    letter = make_letter(days[0].isoformat(), [row])

    runner = BacktestRunner(cfg)
    result = runner.run(
        start=days[0], end=days[-1], letters=[letter],
        minute_bars={"TEST": bars}, daily_bars={"TEST": daily},
    )
    rt = next(iter(result.runtimes.values()))
    assert rt.status == "no_chase_skip"
    assert rt.position is None
    assert result.fills == []


def test_duplicate_symbol_position_not_double_opened(cfg):
    days = trading_days("2026-06-01", "2026-06-05")
    price_by_day = {d: {"open": 100.0, "close": 100.0} for d in days}
    bars = make_minute_bars("TEST", days, price_by_day)
    daily = make_daily_bars(bars)

    row1 = TacticalSummaryRow(
        market="TEST long A", trade_type="long_equity", view="bullish",
        entry_trigger=EntryTrigger(type="entered", level=100.0, condition="entered ~100", triggered=True),
        stop=LetterStop(type="price", value=90.0), targets=[], sizing_multiplier=1.0,
    )
    row2 = TacticalSummaryRow(
        market="TEST long B", trade_type="long_equity", view="bullish",
        entry_trigger=EntryTrigger(type="entered", level=100.0, condition="entered ~100", triggered=True),
        stop=LetterStop(type="price", value=90.0), targets=[], sizing_multiplier=1.0,
    )
    letter = make_letter(days[0].isoformat(), [row1, row2])

    runner = BacktestRunner(cfg)
    result = runner.run(
        start=days[0], end=days[-1], letters=[letter],
        minute_bars={"TEST": bars}, daily_bars={"TEST": daily},
    )
    statuses = sorted(rt.status for rt in result.runtimes.values())
    assert statuses == ["open", "skipped_duplicate"]
