"""The deterministic guardrail wrapper from 02-kill-switch.md. This is code,
not prompt instructions, and it is the SAME code path in backtest and live —
only the OrderSink it wraps differs (FillSimulator here; a real Alpaca
adapter in a later phase). Nothing about safety in this module depends on
the model behaving.

Guardrails enforced, all deterministic and unconditional:
  - HALT file present -> reject everything.
  - Symbol not on the letter-derived whitelist -> reject.
  - Order type not on the configured whitelist -> reject.
  - Notional over max_order_notional -> reject.
  - Would open a position beyond max_concurrent_positions -> reject.
  - Daily loss limit breached -> reject AND write the HALT file (auto-halt).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from crown.config import Config
from crown.models import Order, Position


class OrderSink(Protocol):
    def submit(self, order: Order) -> Order: ...


@dataclass
class GuardrailCheck:
    passed: bool
    reason: str | None = None


def check_guardrails(
    order: Order,
    cfg: Config,
    whitelist: set[str],
    open_positions: list[Position],
    daily_pnl: float,
) -> GuardrailCheck:
    """Pure function — no I/O, no HALT-file side effects — so it's cheap to
    unit test every branch independently. `daily_pnl` is realized +
    unrealized for the current simulated/actual trading day."""
    g = cfg.guardrails

    if order.symbol not in whitelist:
        return GuardrailCheck(False, f"{order.symbol} is not on the letter-derived whitelist")

    if order.order_type not in g.allowed_order_types:
        return GuardrailCheck(False, f"order_type '{order.order_type}' is not on the allowed list {g.allowed_order_types}")

    if order.notional_estimate > g.max_order_notional:
        return GuardrailCheck(False, (
            f"order notional {order.notional_estimate:.2f} exceeds "
            f"max_order_notional {g.max_order_notional}"
        ))

    opening_new_position = order.side == "buy" and not any(
        p.symbol == order.symbol and p.open for p in open_positions
    )
    if opening_new_position and len({p.symbol for p in open_positions if p.open}) >= g.max_concurrent_positions:
        return GuardrailCheck(False, (
            f"opening {order.symbol} would exceed max_concurrent_positions "
            f"({g.max_concurrent_positions})"
        ))

    if daily_pnl <= -abs(g.daily_loss_limit):
        return GuardrailCheck(False, (
            f"daily loss limit breached (P&L {daily_pnl:.2f} <= "
            f"-{g.daily_loss_limit}); trading halted for the day"
        ))

    return GuardrailCheck(True, None)


class OrderGateway:
    """Wraps an OrderSink. Every order passes through here regardless of
    whether it was human-approved (live) or policy-approved (backtest) —
    approval and guardrails are independent gates, and the guardrails apply
    unconditionally, exactly as 06-sizing-and-alternatives.md states: "The
    coded max-notional guardrail still caps the final order regardless."
    """

    def __init__(
        self,
        cfg: Config,
        sink: OrderSink,
        whitelist: set[str],
        open_positions_provider: Callable[[], list[Position]],
        daily_pnl_provider: Callable[[], float],
    ):
        self.cfg = cfg
        self.sink = sink
        self.whitelist = whitelist
        self.open_positions_provider = open_positions_provider
        self.daily_pnl_provider = daily_pnl_provider

    def _halt_file_path(self) -> Path:
        p = Path(self.cfg.guardrails.halt_file)
        if p.is_absolute():
            return p
        from crown.config import REPO_ROOT
        return REPO_ROOT / p

    def is_halted(self) -> bool:
        return self._halt_file_path().exists()

    def halt(self, reason: str) -> None:
        path = self._halt_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reason, encoding="utf-8")

    def submit(self, order: Order) -> Order:
        if self.is_halted():
            order.status = "rejected_guardrail"
            order.rejection_reason = "HALT file present; refusing to trade"
            return order

        daily_pnl = self.daily_pnl_provider()
        check = check_guardrails(
            order, self.cfg, self.whitelist,
            self.open_positions_provider(), daily_pnl,
        )
        if not check.passed:
            order.status = "rejected_guardrail"
            order.rejection_reason = check.reason
            if daily_pnl <= -abs(self.cfg.guardrails.daily_loss_limit):
                self.halt(f"daily loss limit breached: P&L {daily_pnl:.2f}")
            return order

        return self.sink.submit(order)
