"""Thesis-first letter model (docs/03-letter-model.md).

This is the exact shape the extractor LLM fills via structured outputs, so it
follows OpenAI strict-schema rules: every field is required, "not stated in
the letter" is expressed as an explicit null, and there are no free-form
dict fields. Defaults are deliberately absent — a missing value must be a
visible `null` chosen by the extractor, never something pydantic filled in.

Fields set by code rather than the LLM (received_at, extractor_version, the
model id) live in `ExtractionRecord`, not here.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Source(BaseModel):
    page: int | None
    quote: str


class Evidence(BaseModel):
    kind: Literal["positioning", "flow", "vol", "macro", "technical", "fundamental", "other"]
    detail: str
    source: Source


class InvalidationCondition(BaseModel):
    kind: Literal["price_level", "event_outcome", "regime", "narrative"]
    instrument: str | None
    op: Literal["close_above", "close_below", "trade_above", "trade_below"] | None
    level: float | None
    band_low: float | None
    band_high: float | None
    sessions: int | None
    event_ref: str | None
    text: str
    source: Source


class Conviction(BaseModel):
    dots: int | None
    raw: str | None
    flag: Literal["ok", "glyph_count_mismatch", "not_stated"]


class Thesis(BaseModel):
    thesis_key: str
    title: str
    claim: str
    drivers: list[str]
    evidence: list[Evidence]
    horizon: Literal["days", "weeks", "months", "unspecified"]
    invalidation: list[InvalidationCondition]
    conviction: Conviction
    house_view_relation: Literal["aligned", "conflicts", "unrelated", "unclear"]
    source: Source


class Instrument(BaseModel):
    primary: str
    alternatives: list[str]
    asset_class: Literal[
        "equity", "etf", "future", "fx", "index", "option_on_equity",
        "option_on_future", "crypto", "other",
    ]


class Leg(BaseModel):
    action: Literal["buy", "sell"]
    right: Literal["call", "put", "stock", "future"]
    underlying: str
    strike: float | None
    expiry: str | None
    ratio: float | None
    ratio_kind: Literal["contracts", "shares", "notional_weight", "not_stated"]


class Structure(BaseModel):
    type: Literal[
        "long", "short", "bear_call", "bull_put", "iron_condor",
        "debit_call_spread", "debit_put_spread", "long_call", "long_put",
        "butterfly", "seagull", "pair", "other",
    ]
    legs: list[Leg]
    net_price: float | None
    net_price_kind: Literal["debit", "credit", "price", "spread_level", "unknown"]
    net_price_context: Literal["entry", "target", "illustration", "mark", "not_stated"]
    max_loss_per_unit: float | None
    max_loss_unit: Literal["quoted_points", "contract_currency", "not_stated"]
    max_gain_per_unit: float | None
    breakeven_low: float | None
    breakeven_high: float | None


class Entry(BaseModel):
    type: Literal["entered", "conditional"]
    level: float | None
    band_low: float | None
    band_high: float | None
    condition: str | None
    triggered: bool
    window_close: str | None


class Stop(BaseModel):
    type: Literal["price", "dollar_loss", "behavioral", "none_stated"]
    level: float | None
    dollar: float | None
    rule: str | None
    sessions: int | None


class Target(BaseModel):
    tier: Literal["T1", "T2", "T3"]
    level: float | None
    action: str | None


class ReportedTerm(BaseModel):
    """Literal terms preserve ranges/contexts that cannot fit a scalar field."""
    label: str
    raw: str
    source: Source


class CrownPosition(BaseModel):
    position_key: str
    thesis_key: str
    status: Literal["entered", "conditional", "closed"]
    setup_state: Literal["active", "watch_only", "superseded", "closed", "unclear"]
    instrument: Instrument
    structure: Structure
    view: Literal["bullish", "bearish", "neutral_range", "relative_value", "hedge"]
    entry: Entry
    stop: Stop
    targets: list[Target]
    sizing_multiplier: float | None
    event_dependency: str | None
    management_notes: str | None
    hedges_position_keys: list[str]
    reported_terms: list[ReportedTerm]
    source: Source


class LifecycleUpdate(BaseModel):
    position_ref: str
    kind: Literal[
        "closed", "stop_moved", "target_moved", "resized", "rolled",
        "still_open", "invalidated", "other",
    ]
    detail: str
    exit_level: float | None
    reported_pnl: str | None
    source: Source


class Level(BaseModel):
    instrument: str
    level: float
    kind: Literal[
        "zero_gamma", "put_wall", "call_wall", "support", "resistance",
        "battleground", "pivot", "other",
    ]
    meaning: str
    source: Source


class Event(BaseModel):
    name: str
    at: str | None
    hits: str | None
    affects: list[str]
    source: Source


class HouseView(BaseModel):
    asset_class: str
    view: str
    driver: str | None
    source: Source


class ReviewFlag(BaseModel):
    field: str
    reason: str


class PageCoverage(BaseModel):
    page: int
    sections: list[str]
    position_keys: list[str]
    notes: str | None


class LetterExtraction(BaseModel):
    letter_date: str
    is_mid_cycle: bool
    section_names_seen: list[str]
    theses: list[Thesis]
    crown_positions: list[CrownPosition]
    lifecycle_updates: list[LifecycleUpdate]
    levels: list[Level]
    events: list[Event]
    house_view: list[HouseView]
    review_flags: list[ReviewFlag]
    page_coverage: list[PageCoverage]


class ExtractionRecord(BaseModel):
    """Envelope written to disk: the LLM's extraction plus everything code
    knows that the LLM must not be trusted to state."""
    pdf_name: str
    pdf_sha256: str
    received_at: str | None
    extractor_version: str
    model: str
    extracted_at: str
    usage: dict
    extraction: LetterExtraction
    requested_model: str | None = None
    request_fingerprint: str | None = None
    corrections: list[dict] = Field(default_factory=list)  # code-owned, never LLM output
