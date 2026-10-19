"""Every guardrail in 02-kill-switch.md must demonstrably reject, on its own,
independent of the LLM's judgment. These tests exercise OrderGateway
directly with a fake sink — no market data, no API calls."""
from __future__ import annotations

import dataclasses

from crown.engine.orders import OrderGateway, check_guardrails
from crown.models import Order, Position, StopState


def make_order(**overrides) -> Order:
    defaults = dict(
        order_id="o1", trade_id="t1", symbol="MSFT", side="buy",
        order_type="limit", qty=1, limit_price=400.0,
        notional_estimate=100.0, created_at="2026-06-22T09:30:00",
    )
    defaults.update(overrides)
    return Order(**defaults)


class FakeSink:
    def __init__(self):
        self.received: list[Order] = []

    def submit(self, order: Order) -> Order:
        self.received.append(order)
        order.status = "filled"
        return order


def make_gateway(cfg, whitelist=frozenset({"MSFT"}), positions=None, daily_pnl=0.0):
    sink = FakeSink()
    gw = OrderGateway(
        cfg=cfg, sink=sink, whitelist=set(whitelist),
        open_positions_provider=lambda: positions or [],
        daily_pnl_provider=lambda: daily_pnl,
    )
    return gw, sink


def test_halt_file_blocks_every_order(cfg):
    gw, sink = make_gateway(cfg)
    gw.halt("manual test halt")
    order = gw.submit(make_order())
    assert order.status == "rejected_guardrail"
    assert "HALT" in order.rejection_reason
    assert sink.received == []


def test_symbol_not_on_whitelist_rejected(cfg):
    gw, sink = make_gateway(cfg, whitelist=frozenset({"AAPL"}))
    order = gw.submit(make_order(symbol="MSFT"))
    assert order.status == "rejected_guardrail"
    assert "whitelist" in order.rejection_reason
    assert sink.received == []


def test_order_type_not_allowed_rejected(cfg):
    # "market" is a schema-valid OrderType, but config can narrow the
    # allowed set further — the guardrail must respect config, not just the
    # Pydantic Literal (which itself already blocks anything not in
    # {limit, stop_limit, bracket, market} before an Order can even be built).
    narrowed = dataclasses.replace(
        cfg, guardrails=dataclasses.replace(cfg.guardrails, allowed_order_types=["limit"]),
    )
    gw, sink = make_gateway(narrowed)
    order = gw.submit(make_order(order_type="market"))
    assert order.status == "rejected_guardrail"
    assert "order_type" in order.rejection_reason
    assert sink.received == []


def test_notional_over_max_rejected(cfg):
    gw, sink = make_gateway(cfg)
    over = cfg.guardrails.max_order_notional + 1
    order = gw.submit(make_order(notional_estimate=over))
    assert order.status == "rejected_guardrail"
    assert "notional" in order.rejection_reason
    assert sink.received == []


def test_notional_at_max_is_allowed(cfg):
    gw, sink = make_gateway(cfg)
    order = gw.submit(make_order(notional_estimate=cfg.guardrails.max_order_notional))
    assert order.status == "filled"
    assert sink.received


def test_max_concurrent_positions_rejects_new_symbol(cfg):
    open_positions = [
        Position(trade_id=f"t{i}", symbol=sym, direction="long", qty=1,
                  entry_price=100.0, entry_at="2026-06-22T09:30:00",
                  stop=StopState(type="price", initial=90.0, current=90.0))
        for i, sym in enumerate(["AAPL", "GOOGL", "NVDA", "AMD"])
    ]
    gw, sink = make_gateway(cfg, whitelist=frozenset({"MSFT"}), positions=open_positions)
    assert cfg.guardrails.max_concurrent_positions == len(open_positions)
    order = gw.submit(make_order(symbol="MSFT", side="buy"))
    assert order.status == "rejected_guardrail"
    assert "max_concurrent_positions" in order.rejection_reason
    assert sink.received == []


def test_max_concurrent_positions_allows_adding_to_existing_symbol(cfg):
    open_positions = [
        Position(trade_id=f"t{i}", symbol=sym, direction="long", qty=1,
                  entry_price=100.0, entry_at="2026-06-22T09:30:00",
                  stop=StopState(type="price", initial=90.0, current=90.0))
        for i, sym in enumerate(["AAPL", "GOOGL", "NVDA", "MSFT"])
    ]
    gw, sink = make_gateway(cfg, whitelist=frozenset({"MSFT"}), positions=open_positions)
    order = gw.submit(make_order(symbol="MSFT", side="buy"))
    assert order.status == "filled"


def test_max_concurrent_positions_allows_selling_to_close(cfg):
    open_positions = [
        Position(trade_id=f"t{i}", symbol=sym, direction="long", qty=1,
                  entry_price=100.0, entry_at="2026-06-22T09:30:00",
                  stop=StopState(type="price", initial=90.0, current=90.0))
        for i, sym in enumerate(["AAPL", "GOOGL", "NVDA", "AMD"])
    ]
    gw, sink = make_gateway(cfg, whitelist=frozenset({"AAPL"}), positions=open_positions)
    order = gw.submit(make_order(symbol="AAPL", side="sell"))
    assert order.status == "filled"


def test_daily_loss_limit_rejects_and_writes_halt(cfg):
    gw, sink = make_gateway(cfg, daily_pnl=-cfg.guardrails.daily_loss_limit - 1)
    assert not gw.is_halted()
    order = gw.submit(make_order())
    assert order.status == "rejected_guardrail"
    assert "daily loss limit" in order.rejection_reason
    assert gw.is_halted()
    assert sink.received == []

    # And now that HALT exists, even an otherwise-fine order is blocked too.
    gw2, sink2 = make_gateway(cfg, daily_pnl=0.0)
    order2 = gw2.submit(make_order())
    assert order2.status == "rejected_guardrail"
    assert "HALT" in order2.rejection_reason


def test_valid_order_passes_through_to_sink(cfg):
    gw, sink = make_gateway(cfg)
    order = gw.submit(make_order())
    assert order.status == "filled"
    assert sink.received == [order]


def test_check_guardrails_is_pure_and_side_effect_free(cfg):
    order = make_order(notional_estimate=cfg.guardrails.max_order_notional + 1)
    result = check_guardrails(order, cfg, {"MSFT"}, [], 0.0)
    assert not result.passed
    assert "notional" in result.reason
    # Order object itself is untouched by the pure check.
    assert order.status == "pending_approval"
