"""Pure trigger/stop/target/tightening logic — 07-scheduling.md. No I/O, no
Claude calls (the whole point of the compile-once design: everything here is
a dumb price comparison). backtest/runner.py drives simulated time and calls
these functions; they never touch the clock or the network themselves, which
is what makes them unit-testable without any market data or API keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from crown.config import Config
from crown.models import PlannedTarget, PlannedTrade, Position, StopState


def distance_to_entry(trade: PlannedTrade, price: float) -> float | None:
    """Fractional distance from `price` to the trade's entry reference,
    used to pick the next check cadence (07-scheduling.md tiering table).
    None if the trade has no usable reference level yet."""
    zone = trade.entry_zone
    if zone.low is not None and zone.high is not None:
        if zone.low <= price <= zone.high:
            return 0.0
        nearer = zone.low if price < zone.low else zone.high
        return abs(price - nearer) / nearer if nearer else None
    if zone.reference_level:
        return abs(price - zone.reference_level) / zone.reference_level
    return None


class EntryOutcome(str, Enum):
    NOT_YET = "not_yet"
    TRIGGER = "trigger"
    NO_CHASE_SKIP = "no_chase_skip"
    WINDOW_CLOSED = "window_closed"


def _window_close(trade: PlannedTrade, cfg: Config) -> date:
    if trade.entry_window_close:
        return date.fromisoformat(trade.entry_window_close)
    open_date = date.fromisoformat(trade.entry_window_open)
    return open_date + timedelta(days=cfg.no_chase.default_watch_days)


def check_entry(trade: PlannedTrade, price: float, now: datetime, cfg: Config) -> EntryOutcome:
    """Doc 06's no-chase doctrine, mechanized:
      - An 'entered_at_letter' row is Crown's own already-open position — we
        only ever check it once, at entry_window_open, for staleness (has
        price already run away since the letter was written).
      - A conditional row is watched continuously until it actually hits its
        level/band, or its window closes with no trigger — in which case it
        is never staged, no matter what happens afterward. This is the
        mechanism that makes an untriggered setup structurally impossible to
        chase, matching "the price you didn't get is not a trade you missed."
    """
    zone = trade.entry_zone
    window_close = _window_close(trade, cfg)

    if zone.trigger_type == "entered_at_letter":
        if now.date() > date.fromisoformat(trade.entry_window_open):
            return EntryOutcome.WINDOW_CLOSED
        ref = zone.reference_level
        if ref is None:
            return EntryOutcome.WINDOW_CLOSED
        if abs(price - ref) / ref <= cfg.no_chase.max_pct_from_trigger:
            return EntryOutcome.TRIGGER
        return EntryOutcome.NO_CHASE_SKIP

    # conditional_level / conditional_band
    if now.date() > window_close:
        return EntryOutcome.WINDOW_CLOSED
    if now.date() < date.fromisoformat(trade.entry_window_open):
        return EntryOutcome.NOT_YET

    if zone.low is not None and zone.high is not None:
        if zone.low <= price <= zone.high:
            return EntryOutcome.TRIGGER
        return EntryOutcome.NOT_YET

    if zone.reference_level is not None:
        # A single conditional level: trigger on touch (either direction —
        # the level was published as the specific price the thesis needs).
        if abs(price - zone.reference_level) / zone.reference_level <= 0.0015:
            return EntryOutcome.TRIGGER
        return EntryOutcome.NOT_YET

    return EntryOutcome.WINDOW_CLOSED


def check_stop_hit(position: Position, price: float) -> bool:
    level = position.stop.current
    if level is None:
        return False
    if position.direction == "long":
        return price <= level
    return price >= level


def check_target_hit(position: Position, price: float) -> PlannedTarget | None:
    for t in position.targets:
        if t.hit:
            continue
        if position.direction == "long" and price >= t.level:
            return t
        if position.direction == "short" and price <= t.level:
            return t
    return None


def ratchet_stop(position: Position, new_level: float) -> bool:
    """One-way ratchet (06-sizing-and-alternatives.md): a long's stop may
    only rise; a short's stop may only fall. Returns True if the stop moved."""
    current = position.stop.current
    if current is None:
        position.stop.current = new_level
        return True
    if position.direction == "long" and new_level > current:
        position.stop.current = new_level
        return True
    if position.direction == "short" and new_level < current:
        position.stop.current = new_level
        return True
    return False


def cadence_for(trade: PlannedTrade, price: float | None, cfg: Config) -> int:
    if price is None:
        return cfg.scheduling.tiers[0].cadence_minutes if cfg.scheduling.tiers[0].max_distance is None \
            else max(t.cadence_minutes for t in cfg.scheduling.tiers)
    dist = distance_to_entry(trade, price)
    if dist is None:
        return max(t.cadence_minutes for t in cfg.scheduling.tiers)
    return cfg.scheduling.cadence_for_distance(dist)
