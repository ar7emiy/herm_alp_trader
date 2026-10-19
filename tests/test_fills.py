from __future__ import annotations

from crown.engine.fills import (
    compute_proxy_shares,
    equity_entry_fill,
    settle_options_at_expiry,
    stop_exit_fill,
    target_exit_fill,
)
from crown.models import OptionLeg, PlannedSize, PlannedTrade, EntryZone, StopState


def make_mu_condor() -> PlannedTrade:
    legs = [
        OptionLeg(action="sell", right="put", strike=900, expiry="2026-06-26"),
        OptionLeg(action="buy", right="put", strike=800, expiry="2026-06-26"),
        OptionLeg(action="sell", right="call", strike=1350, expiry="2026-06-26"),
        OptionLeg(action="buy", right="call", strike=1450, expiry="2026-06-26"),
    ]
    return PlannedTrade(
        trade_id="t1", source="featured", crown_symbol="MU", traded_symbol="MU",
        asset_class="options_spread", direction="long", structure="iron_condor",
        legs=legs, credit_or_debit=18.0, max_risk_per_unit=81.8,
        entry_zone=EntryZone(trigger_type="conditional_band", low=1085, high=1180),
        entry_window_open="2026-06-22",
        stop=StopState(type="behavioral"),
        size=PlannedSize(contracts=1, risk_dollars=56.25, fits=True, method="crown_exact"),
        action="watchlist",
    )


def test_condor_expires_worthless_keeps_full_credit():
    trade = make_mu_condor()
    pnl = settle_options_at_expiry(trade, underlying_close=1100.0, contracts=1)
    assert pnl == 1800.0  # full $18 credit * 100, both wings untouched


def test_condor_breached_upper_wing_loses_width_minus_credit():
    trade = make_mu_condor()
    pnl = settle_options_at_expiry(trade, underlying_close=1500.0, contracts=1)
    # width 100, credit 18 -> max loss 82/contract = -8200
    assert pnl == -8200.0


def test_condor_breached_lower_wing_symmetric_max_loss():
    trade = make_mu_condor()
    pnl = settle_options_at_expiry(trade, underlying_close=700.0, contracts=1)
    assert pnl == -8200.0


def test_condor_scales_with_contracts():
    trade = make_mu_condor()
    pnl = settle_options_at_expiry(trade, underlying_close=1100.0, contracts=3)
    assert pnl == 1800.0 * 3


def test_settlement_returns_none_without_legs():
    trade = make_mu_condor()
    trade.legs = []
    assert settle_options_at_expiry(trade, 1100.0, 1) is None


def test_equity_entry_fill_uses_bar_close():
    bar = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.5}
    assert equity_entry_fill(bar) == 101.5


def test_stop_exit_fill_long_no_gap():
    bar = {"open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0}
    assert stop_exit_fill(bar, stop_level=99.5, direction="long") == 99.5


def test_stop_exit_fill_long_gapped_through():
    # Overnight gap: opens below the stop -> filled at the worse open price.
    bar = {"open": 95.0, "high": 96.0, "low": 93.0, "close": 94.0}
    assert stop_exit_fill(bar, stop_level=99.5, direction="long") == 95.0


def test_stop_exit_fill_short_gapped_through():
    bar = {"open": 110.0, "high": 112.0, "low": 108.0, "close": 111.0}
    assert stop_exit_fill(bar, stop_level=105.0, direction="short") == 110.0


def test_target_exit_fill_is_exact():
    assert target_exit_fill(50.0) == 50.0


def test_compute_proxy_shares_with_known_stop_price():
    shares, stop_used = compute_proxy_shares(
        risk_dollars=75.0, entry_price=400.0, stop_price=392.0,
        direction="long", default_stop_pct=0.03,
    )
    assert stop_used == 392.0
    assert shares == int(75.0 // 8.0)


def test_compute_proxy_shares_falls_back_to_default_pct():
    shares, stop_used = compute_proxy_shares(
        risk_dollars=75.0, entry_price=100.0, stop_price=None,
        direction="short", default_stop_pct=0.03,
    )
    assert stop_used == 103.0  # short: stop sits above entry
    assert shares == int(75.0 // 3.0)
