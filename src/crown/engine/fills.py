"""Conservative fill simulation on OHLC minute bars, and options expiry
settlement. Pure functions — no I/O — so the settlement math (the part most
worth getting right) is unit-testable without any market data.

Options P&L is the one place this backtest is explicitly approximate
(per the user's agreed scope): entries are assumed filled at the letter's
stated credit/debit once the underlying enters the entry band, and only
FEATURED trades (which carry real strikes) get true intrinsic-value
settlement at expiry. Everything else is documented in the report as an
approximation, never silently treated as exact.
"""
from __future__ import annotations

from crown.models import OptionLeg, PlannedTrade


def equity_entry_fill(bar) -> float:
    """Conservative: fill at the bar's close, not the touch price, so the
    backtest never assumes a better fill than a live system plausibly gets
    reacting to the same bar."""
    return float(bar["close"])


def stop_exit_fill(bar, stop_level: float, direction: str) -> float:
    """Fill at the stop level, or worse if the bar gapped through it —
    mirrors real slippage on a stop-loss rather than assuming a perfect fill."""
    o, lo, hi = float(bar["open"]), float(bar["low"]), float(bar["high"])
    if direction == "long":
        if lo > stop_level:
            return stop_level
        return min(stop_level, o) if o < stop_level else stop_level
    else:
        if hi < stop_level:
            return stop_level
        return max(stop_level, o) if o > stop_level else stop_level


def target_exit_fill(target_level: float) -> float:
    """Limit-style take-profit: fills exactly at the target, never better."""
    return target_level


def compute_proxy_shares(
    risk_dollars: float,
    entry_price: float,
    stop_price: float | None,
    direction: str,
    default_stop_pct: float,
) -> tuple[int, float]:
    """For equity_proxy_pending_price trades: share count and the stop level
    actually used are both computed here, at fill time, because they depend
    on the live entry price build_plan never had access to. Returns
    (shares, stop_level_used)."""
    if stop_price is not None and stop_price > 0:
        distance = abs(entry_price - stop_price)
        stop_level = stop_price
    else:
        distance = entry_price * default_stop_pct
        stop_level = entry_price - distance if direction == "long" else entry_price + distance
    if distance <= 0:
        return 0, stop_level
    return int(risk_dollars // distance), stop_level


def intrinsic_value(leg: OptionLeg, underlying_price: float) -> float:
    if leg.right == "call":
        return max(0.0, underlying_price - leg.strike)
    return max(0.0, leg.strike - underlying_price)


def settle_options_at_expiry(trade: PlannedTrade, underlying_close: float, contracts: int) -> float | None:
    """Exact intrinsic-value settlement — only possible for trades with real
    strikes (featured trades using the crown_exact sizing path). Returns
    None (not zero — a real "unknown" signal) when strikes aren't available,
    so callers never mistake a missing computation for a $0 outcome."""
    if not trade.legs:
        return None
    settlement = sum(
        (-1 if leg.action == "sell" else 1) * intrinsic_value(leg, underlying_close)
        for leg in trade.legs
    )
    net_upfront = trade.credit_or_debit or 0.0
    return (net_upfront + settlement) * 100 * contracts
