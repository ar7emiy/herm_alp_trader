from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal


def D(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite financial value")
    return result


def serial(value):
    if hasattr(value, "model_dump"):
        return serial(value.model_dump())
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {k: serial(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def digest(value) -> str:
    return hashlib.sha256(json.dumps(serial(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ResearchPolicy:
    name: str = "strict"
    initial_cash: Decimal = Decimal("5000")
    reserve_fraction: Decimal = Decimal("0.10")
    risk_unit: Decimal = Decimal("75")
    max_order_notional: Decimal = Decimal("2000")
    max_positions: int = 4
    max_open_risk: Decimal = Decimal("300")
    max_thesis_risk: Decimal = Decimal("112.50")
    max_cluster_risk: Decimal = Decimal("150")
    daily_loss_limit: Decimal = Decimal("150")
    weekly_loss_limit: Decimal = Decimal("300")
    max_chase: Decimal = Decimal("0.05")
    share_step: Decimal = Decimal("1")
    slippage_bps: Decimal = Decimal("5")
    fee_bps: Decimal = Decimal("1")
    max_letter_age_sessions: int = 5
    allow_unanchored_holdings: bool = False
    atr_protection: bool = False
    atr_multiple: Decimal = Decimal("2")
    trail_multiple: Decimal = Decimal("3")
    atr_sessions: int = 14

    def __post_init__(self):
        if any(isinstance(v,Decimal) and not v.is_finite() for v in self.__dict__.values()):
            raise ValueError("Non-finite policy value")
        for field_name in ("initial_cash", "risk_unit", "max_order_notional", "max_open_risk", "max_thesis_risk", "max_cluster_risk", "share_step"):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if not 0 <= self.reserve_fraction < 1 or self.max_positions < 1:
            raise ValueError("Invalid portfolio policy")
        if self.slippage_bps < 0 or self.fee_bps < 0:
            raise ValueError("Costs cannot be negative")
        if self.max_letter_age_sessions<1 or self.atr_sessions<1 or self.daily_loss_limit<=0 or self.weekly_loss_limit<=0:
            raise ValueError("Evidence, ATR windows and loss limits must be positive")
        if self.atr_multiple <= 0 or self.trail_multiple <= 0 or self.max_chase < 0:
            raise ValueError("ATR multipliers must be positive and chase bound nonnegative")

    @classmethod
    def protective_research(cls, **kwargs):
        return cls(name="protective_research", allow_unanchored_holdings=True,
                   atr_protection=True, share_step=Decimal("0.001"), **kwargs)


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open_at: datetime
    close_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self):
        if not self.open_at.tzinfo or not self.close_at.tzinfo or self.open_at >= self.close_at:
            raise ValueError("Bar times must be aware and ordered")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("Invalid bar values")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("Invalid OHLC range")


@dataclass(frozen=True)
class Plan:
    position_id: str
    thesis_id: str
    symbol: str
    available_at: datetime
    expires_on: date
    letter_date: str
    record_sha256: str
    crown_key: str
    source: dict
    anchor: Decimal | None
    multiplier: Decimal
    stop: Decimal | None
    stop_kind: str
    stop_sessions: int
    targets: tuple[Decimal, ...]
    deviations: tuple[str, ...] = ()
    allowed: bool = True
    reasons: tuple[str, ...] = ()
    invalidation: tuple[tuple[str, Decimal, int], ...] = ()


@dataclass
class Holding:
    position_id: str
    plan: Plan
    quantity: Decimal
    cost_basis: Decimal
    entry_price: Decimal
    initial_stop: Decimal
    stop: Decimal
    protection_kind: str
    mark: Decimal
    entered_at: datetime
    best_close: Decimal
    target_index: int = 0
    close_breaches: int = 0
    realized_pnl: Decimal = Decimal("0")
    pending_exit: str | None = None
    invalidation_breaches: dict[int, int] = field(default_factory=dict)

    @property
    def risk(self):
        return max(Decimal("0"), self.mark - self.stop) * self.quantity


@dataclass(frozen=True)
class Reservation:
    intent_id: str
    position_id: str
    thesis_id: str
    symbol: str
    quantity: Decimal
    limit: Decimal
    stop: Decimal
    protection_kind: str
    cash: Decimal
    risk: Decimal


@dataclass
class Portfolio:
    settled_cash: Decimal
    unsettled: list[tuple[date, Decimal]] = field(default_factory=list)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    holdings: dict[str, Holding] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    applied_fills: set[str] = field(default_factory=set)

    @property
    def reserved_cash(self):
        return sum((r.cash for r in self.reservations.values()), Decimal("0"))

    @property
    def equity(self):
        return self.settled_cash + sum((v for _, v in self.unsettled), Decimal("0")) + sum((h.quantity * h.mark for h in self.holdings.values()), Decimal("0"))

    @property
    def open_risk(self):
        return sum((h.risk for h in self.holdings.values()), Decimal("0")) + sum((r.risk for r in self.reservations.values()), Decimal("0"))
