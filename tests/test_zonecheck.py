from __future__ import annotations

from datetime import datetime, timezone

from crown.config import load_config
from crown.engine.zonecheck import (
    EntryOutcome,
    cadence_for,
    check_entry,
    check_stop_hit,
    check_target_hit,
    distance_to_entry,
    ratchet_stop,
)
from crown.models import EntryZone, PlannedSize, PlannedTarget, PlannedTrade, Position, StopState

CFG = load_config()


def make_trade(**overrides) -> PlannedTrade:
    defaults = dict(
        trade_id="t1", source="tactical", crown_symbol="MSFT", traded_symbol="MSFT",
        asset_class="equity", direction="long", structure="long_equity",
        entry_zone=EntryZone(trigger_type="entered_at_letter", reference_level=400.0),
        entry_window_open="2026-06-22",
        stop=StopState(type="price", initial=390.0, current=390.0),
        size=PlannedSize(shares=1, risk_dollars=75.0, fits=True, method="crown_exact"),
        action="watchlist",
    )
    defaults.update(overrides)
    return PlannedTrade(**defaults)


def dt(day: str, hour: int = 10) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=timezone.utc)


# --- entered_at_letter: no-chase staleness check -----------------------

def test_entered_at_letter_stages_when_price_still_near():
    trade = make_trade()
    outcome = check_entry(trade, price=402.0, now=dt("2026-06-22"), cfg=CFG)
    assert outcome == EntryOutcome.TRIGGER


def test_entered_at_letter_no_chase_skip_when_price_ran_away():
    trade = make_trade()
    outcome = check_entry(trade, price=450.0, now=dt("2026-06-22"), cfg=CFG)
    assert outcome == EntryOutcome.NO_CHASE_SKIP


def test_entered_at_letter_only_checked_on_its_own_day():
    trade = make_trade()
    outcome = check_entry(trade, price=402.0, now=dt("2026-06-23"), cfg=CFG)
    assert outcome == EntryOutcome.WINDOW_CLOSED


# --- conditional: must actually trigger, never chased -------------------

def test_conditional_band_not_yet_outside_band():
    trade = make_trade(entry_zone=EntryZone(trigger_type="conditional_band", low=1085, high=1180))
    outcome = check_entry(trade, price=1200.0, now=dt("2026-06-22"), cfg=CFG)
    assert outcome == EntryOutcome.NOT_YET


def test_conditional_band_triggers_inside_band():
    trade = make_trade(entry_zone=EntryZone(trigger_type="conditional_band", low=1085, high=1180))
    outcome = check_entry(trade, price=1120.0, now=dt("2026-06-22"), cfg=CFG)
    assert outcome == EntryOutcome.TRIGGER


def test_usdjpy_style_conditional_never_triggers_stays_not_yet():
    # 157.50 conditional short, price stays well below the trigger the whole
    # letter cycle — mirrors the June 22 USDJPY row (which in build_plan is
    # already skipped for lack of a data source, but the mechanism here must
    # independently refuse to ever stage an untouched conditional level).
    trade = make_trade(
        entry_zone=EntryZone(trigger_type="conditional_level", reference_level=157.50),
        entry_window_close="2026-06-29",
    )
    outcome = check_entry(trade, price=152.0, now=dt("2026-06-24"), cfg=CFG)
    assert outcome == EntryOutcome.NOT_YET


def test_conditional_window_closes_without_trigger_never_stages():
    trade = make_trade(
        entry_zone=EntryZone(trigger_type="conditional_level", reference_level=157.50),
        entry_window_close="2026-06-23",
    )
    # Even though price finally reaches the level, it's after the window closed.
    outcome = check_entry(trade, price=157.5, now=dt("2026-06-24"), cfg=CFG)
    assert outcome == EntryOutcome.WINDOW_CLOSED


def test_mu_style_window_closes_wednesday_after_monday_tuesday_entry_only():
    # Doc 03's exact regression scenario: MU's entry window is Mon/Tue; a
    # Wednesday check must mark it closed, not enter late.
    trade = make_trade(
        entry_zone=EntryZone(trigger_type="conditional_band", low=1085, high=1180),
        entry_window_open="2026-06-22", entry_window_close="2026-06-23",
    )
    outcome = check_entry(trade, price=1120.0, now=dt("2026-06-24"), cfg=CFG)
    assert outcome == EntryOutcome.WINDOW_CLOSED


# --- default watch window backstop --------------------------------------

def test_conditional_with_no_explicit_close_uses_default_watch_days():
    trade = make_trade(
        entry_zone=EntryZone(trigger_type="conditional_level", reference_level=100.0),
        entry_window_close=None,
    )
    still_open = check_entry(trade, price=50.0, now=dt("2026-07-01"), cfg=CFG)  # 9 days later
    assert still_open == EntryOutcome.NOT_YET
    closed = check_entry(trade, price=50.0, now=dt("2026-07-10"), cfg=CFG)  # 18 days later
    assert closed == EntryOutcome.WINDOW_CLOSED


# --- stop / target checks -------------------------------------------------

def make_position(**overrides) -> Position:
    defaults = dict(
        trade_id="t1", symbol="MSFT", direction="long", qty=1,
        entry_price=400.0, entry_at="2026-06-22T14:30:00",
        stop=StopState(type="price", initial=390.0, current=390.0),
        targets=[PlannedTarget(tier="T1", level=420.0), PlannedTarget(tier="T2", level=440.0)],
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_long_stop_hit_below_level():
    pos = make_position()
    assert check_stop_hit(pos, price=389.0)
    assert not check_stop_hit(pos, price=391.0)


def test_short_stop_hit_above_level():
    pos = make_position(direction="short", stop=StopState(type="price", initial=410.0, current=410.0))
    assert check_stop_hit(pos, price=411.0)
    assert not check_stop_hit(pos, price=409.0)


def test_target_hit_returns_first_untouched_tier():
    pos = make_position()
    hit = check_target_hit(pos, price=421.0)
    assert hit.tier == "T1"


def test_target_already_hit_is_skipped():
    pos = make_position()
    pos.targets[0].hit = True
    hit = check_target_hit(pos, price=421.0)
    assert hit is None  # price is above T1 (already hit) but hasn't reached T2 (440)


def test_ratchet_long_only_raises():
    pos = make_position()
    assert ratchet_stop(pos, 395.0) is True
    assert pos.stop.current == 395.0
    assert ratchet_stop(pos, 393.0) is False  # would lower — rejected
    assert pos.stop.current == 395.0


def test_ratchet_short_only_lowers():
    pos = make_position(direction="short", stop=StopState(type="price", initial=410.0, current=410.0))
    assert ratchet_stop(pos, 405.0) is True
    assert pos.stop.current == 405.0
    assert ratchet_stop(pos, 408.0) is False  # would raise — rejected
    assert pos.stop.current == 405.0


# --- adaptive tightening cadence -----------------------------------------

def test_cadence_tightens_as_price_nears_entry():
    trade = make_trade(entry_zone=EntryZone(trigger_type="conditional_level", reference_level=100.0))
    far = cadence_for(trade, price=90.0, cfg=CFG)   # 10% away
    near = cadence_for(trade, price=99.5, cfg=CFG)  # 0.5% away
    assert far == 60
    assert near == 12
    assert near < far


def test_distance_to_entry_zero_inside_band():
    trade = make_trade(entry_zone=EntryZone(trigger_type="conditional_band", low=1085, high=1180))
    assert distance_to_entry(trade, 1120.0) == 0.0
