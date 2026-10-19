"""Two families of models, kept in one file because they mirror each other:

1. Letter-extraction schema (`ParsedLetter` and friends) — the shape Claude
   fills in from a PDF via structured outputs. Matches 05-letter-schema.md
   field-for-field, with one deliberate extension: `entry_window_close` on
   trade rows, so the compiler can encode Crown's "entry window shuts fast"
   doctrine (06-sizing-and-alternatives.md) without a second LLM call.

2. Compiled-plan schema (`ContingentPlan` and friends) — the deterministic,
   fully-priced-in output of `compiler/build_plan.py`. Nothing in this half
   is produced by an LLM; it's the artifact the backtest/live executor
   replays against price data with zero further reasoning calls.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 1. Letter-extraction schema (mirrors 05-letter-schema.md)
# ---------------------------------------------------------------------------

TradeType = Literal[
    "bear_call", "bull_put", "iron_condor", "debit_spread",
    "seagull", "pair", "long_equity", "other",
]
View = Literal["bullish", "bearish", "breakdown_hedge", "pair"]
ConvictionFlag = Literal["ok", "glyph_count_mismatch"]
EntryTriggerType = Literal["entered", "conditional"]
StopType = Literal["price", "dollar_loss", "behavioral"]
TradeStatus = Literal["planned", "entered"]
Cohort = Literal["managed_money", "dealers", "commercials"]


class CalendarEvent(BaseModel):
    event: str
    datetime: str | None = None
    hits: str | None = None
    affects_trades: list[str] = Field(default_factory=list)


class EntryTrigger(BaseModel):
    type: EntryTriggerType
    level: float | None = None
    condition: str | None = None
    triggered: bool


class Stop(BaseModel):
    type: StopType
    value: float | None = None
    rule: str | None = None


class Target(BaseModel):
    tier: Literal["T1", "T2", "T3"]
    level: float | None = None


class TacticalSummaryRow(BaseModel):
    market: str
    instrument_alternatives: list[str] = Field(default_factory=list)
    trade_type: TradeType
    view: View
    entry_trigger: EntryTrigger
    stop: Stop
    targets: list[Target] = Field(default_factory=list)
    sizing_multiplier: float | None = None
    # Extension beyond doc 05: if the letter's text ties this trade's entry
    # timing to an upcoming event ("MU earnings Wed 6/24"), extract the date
    # after which entry is no longer valid. Null if no such constraint.
    entry_window_close: str | None = None


class OptionLeg(BaseModel):
    action: Literal["sell", "buy"]
    right: Literal["call", "put"]
    strike: float
    expiry: str


class Breakevens(BaseModel):
    down: float | None = None
    up: float | None = None


class EntryConditions(BaseModel):
    underlying_low: float | None = None
    underlying_high: float | None = None
    min_credit: float | None = None


class FeaturedTrade(BaseModel):
    symbol: str
    strategy: str
    conviction: str
    conviction_raw: str
    conviction_flag: ConvictionFlag
    status: TradeStatus
    event: str | None = None
    expiry: str | None = None
    legs: list[OptionLeg] = Field(default_factory=list)
    target_credit: float | None = None
    min_credit: float | None = None
    skip_below: float | None = None
    max_profit: float | None = None
    max_risk: float | None = None
    breakevens: Breakevens | None = None
    entry_conditions: EntryConditions | None = None
    sizing_multiplier: float | None = None
    management: str | None = None
    invalidation: str | None = None
    entry_window_close: str | None = None


class PositioningRow(BaseModel):
    asset: str
    cohort: Cohort
    metric: str
    percentile: float | None = None
    read: str | None = None


class NotableFlowRow(BaseModel):
    symbol: str
    description: str
    read: str | None = None


class PreviousWeekReviewRow(BaseModel):
    symbol: str
    outcome: str
    pnl: str | None = None


class HouseViewRow(BaseModel):
    asset_class: str
    view: str
    driver: str | None = None


class KeyLevel(BaseModel):
    symbol: str
    level: float
    meaning: str


class ParsedLetter(BaseModel):
    letter_date: str
    is_mid_cycle: bool
    calendar: list[CalendarEvent] = Field(default_factory=list)
    tactical_summary: list[TacticalSummaryRow] = Field(default_factory=list)
    featured_trades: list[FeaturedTrade] = Field(default_factory=list)
    positioning: list[PositioningRow] = Field(default_factory=list)
    notable_flow: list[NotableFlowRow] = Field(default_factory=list)
    previous_week_review: list[PreviousWeekReviewRow] = Field(default_factory=list)
    house_view: list[HouseViewRow] = Field(default_factory=list)
    key_levels: list[KeyLevel] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. Compiled-plan schema — produced deterministically by build_plan.py
# ---------------------------------------------------------------------------

AssetClass = Literal["equity", "etf", "options_spread", "fx_or_futures_no_data"]
SizingMethod = Literal[
    "crown_exact", "equity_proxy", "equity_proxy_pending_price", "skipped",
]
PlanAction = Literal["watchlist", "skipped"]
Direction = Literal["long", "short"]
ZoneKind = Literal["entry_low", "entry_high", "invalidation", "target", "regime"]


class EntryZone(BaseModel):
    """The band the zone-check compares live price against. For an
    already-`entered` letter row this is level +/- the no-chase tolerance;
    for a `conditional` row it's the published trigger (as a point, or the
    entry_conditions band for options)."""
    low: float | None = None
    high: float | None = None
    trigger_type: Literal["entered_at_letter", "conditional_level", "conditional_band"]
    reference_level: float | None = None


class PlannedSize(BaseModel):
    shares: int | None = None
    contracts: int | None = None
    risk_dollars: float
    fits: bool
    method: SizingMethod
    notes: list[str] = Field(default_factory=list)


class PlannedTarget(BaseModel):
    tier: Literal["T1", "T2", "T3"]
    level: float
    hit: bool = False


class StopState(BaseModel):
    """Carries the doc-06 one-way ratchet. `current` starts at the initial
    stop and may only move in the direction that reduces risk."""
    type: StopType
    initial: float | None = None
    current: float | None = None
    rule: str | None = None
    behavioral_sessions_required: int | None = None
    behavioral_sessions_count: int = 0


class PlannedTrade(BaseModel):
    trade_id: str
    source: Literal["featured", "tactical"]
    crown_symbol: str
    traded_symbol: str | None = None
    asset_class: AssetClass
    direction: Direction
    structure: TradeType
    legs: list[OptionLeg] = Field(default_factory=list)
    credit_or_debit: float | None = None
    max_risk_per_unit: float | None = None
    expiry: str | None = None  # options settlement date; None for non-options trades

    entry_zone: EntryZone
    entry_window_open: str
    entry_window_close: str | None = None

    stop: StopState
    targets: list[PlannedTarget] = Field(default_factory=list)
    half_off_at_t1: bool = True

    size: PlannedSize

    conviction: str | None = None
    conviction_flag: ConvictionFlag | None = None
    sizing_multiplier_letter: float | None = None

    action: PlanAction
    skip_reason: str | None = None
    house_view_conflict_note: str | None = None
    symbol_resolution_note: str | None = None
    alt_finder_log: list[str] = Field(default_factory=list)


class WatchZone(BaseModel):
    symbol: str
    kind: ZoneKind
    level: float
    meaning: str
    linked_trade_id: str | None = None


class ContingentPlan(BaseModel):
    letter_date: str
    compiled_at: str
    trades: list[PlannedTrade] = Field(default_factory=list)
    watch_zones: list[WatchZone] = Field(default_factory=list)
    ticker_whitelist: list[str] = Field(default_factory=list)
    house_view: list[HouseViewRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. Execution-time models — orders, fills, positions, trade log rows
# ---------------------------------------------------------------------------

OrderType = Literal["limit", "stop_limit", "bracket", "market"]
OrderSide = Literal["buy", "sell"]
OrderStatus = Literal[
    "pending_approval", "approved", "rejected_guardrail", "rejected_approval",
    "filled", "expired", "cancelled",
]


class Order(BaseModel):
    order_id: str
    trade_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: float
    limit_price: float | None = None
    stop_price: float | None = None
    notional_estimate: float
    created_at: str
    status: OrderStatus = "pending_approval"
    rejection_reason: str | None = None


class Fill(BaseModel):
    order_id: str
    trade_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    filled_at: str


class Position(BaseModel):
    trade_id: str
    symbol: str
    direction: Direction
    qty: float
    entry_price: float
    entry_at: str
    stop: StopState
    targets: list[PlannedTarget] = Field(default_factory=list)
    realized_pnl: float = 0.0
    open: bool = True
    closed_at: str | None = None
    close_reason: str | None = None
