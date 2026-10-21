"""Typed evidence for future monitors. Unknown evidence never means thesis holds.

No provider or execution adapter. Public observations/revisions must pass this
time/identity boundary before a monitor can use them. Observation period, release
time and local receipt time are deliberately separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re


def aware(at):
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Evidence timestamps must be timezone aware")


@dataclass(frozen=True)
class Observation:
    series: str
    units: str
    period_at: datetime
    published_at: datetime
    received_at: datetime
    value: Decimal
    provider: str
    source_url: str
    content_sha256: str

    def __post_init__(self):
        for at in (self.period_at, self.published_at, self.received_at):
            aware(at)
        if self.period_at > self.published_at or self.published_at > self.received_at:
            raise ValueError("Evidence must follow observation, publication, receipt order")
        if not self.value.is_finite() or not self.series or not self.units or not self.provider:
            raise ValueError("Evidence requires a finite value and exact identity")
        if not re.fullmatch(r"[a-f0-9]{64}", self.content_sha256) or not self.source_url.startswith("https://"):
            raise ValueError("Evidence requires source URL and content hash")


@dataclass(frozen=True)
class ThresholdRule:
    series: str
    units: str
    op: str
    level: Decimal
    max_release_age: timedelta
    max_observation_age: timedelta
    crown_source: dict

    def __post_init__(self):
        if self.op not in {"above", "below"} or not self.level.is_finite():
            raise ValueError("Only explicit finite above/below thresholds are supported")
        if self.max_release_age <= timedelta(0) or self.max_observation_age <= timedelta(0):
            raise ValueError("Freshness limits must be positive")
        if not self.crown_source.get("page") or not self.crown_source.get("quote"):
            raise ValueError("Monitoring requires a Crown source")


def asof_observations(observations, series, units, at):
    """Select most recent revision actually received by at, per observation period."""
    aware(at)
    selected = {}
    for obs in observations:
        if obs.series != series or obs.units != units or obs.received_at > at:
            continue
        key = (obs.published_at, obs.received_at)
        prior = selected.get(obs.period_at)
        if prior and key == (prior.published_at, prior.received_at) and obs != prior:
            raise ValueError("Conflicting observations at identical release/receipt timestamps")
        if prior is None or key > (prior.published_at, prior.received_at):
            selected[obs.period_at] = obs
    return [selected[t] for t in sorted(selected)]


def evaluate(rule, observations, at):
    values = asof_observations(observations, rule.series, rule.units, at)
    if not values:
        return {"state": "unknown", "reason": "Required series/units not available by decision time"}
    current = values[-1]
    if at-current.published_at > rule.max_release_age or at-current.period_at > rule.max_observation_age:
        return {"state": "unknown", "reason": "Required evidence stale; no thesis-health assumption"}
    broken = current.value > rule.level if rule.op == "above" else current.value < rule.level
    return {"state": "invalidated" if broken else "not_breached",
            "reason": "Only this explicit condition evaluated; not a complete thesis-health assessment",
            "value": str(current.value), "source_sha256": current.content_sha256,
            "published_at": current.published_at.isoformat(), "received_at": current.received_at.isoformat()}


def positioning_percentile(observations, series, units, at, history=52):
    """Empirical percentile vs prior releases; ties receive half weight.

    Values should already be normalized (e.g. net contracts/open interest) using
    matching releases. This never guesses netting, categories, contracts or units.
    """
    if history < 2:
        raise ValueError("History must contain at least two previous observations")
    rows = asof_observations(observations, series, units, at)
    if len(rows) < history+1:
        return None
    current = rows[-1].value
    prior = [r.value for r in rows[-history-1:-1]]
    return (Decimal(sum(v < current for v in prior)) + Decimal('0.5')*sum(v == current for v in prior))/history
