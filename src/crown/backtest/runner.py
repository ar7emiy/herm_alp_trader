"""The backtest event loop. Drives simulated time forward through an
adaptive schedule of check-points (07-scheduling.md) rather than a uniform
per-minute scan — each trade computes its own next wake time, exactly like
the live cron-tightening design, so the replay exercises the same scheduling
logic a live run would use. Nothing here calls Claude: the letter was
compiled once, up front; this loop only does price comparisons, order
guardrail checks, and fills.
"""
from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from crown.compiler.build_plan import build_plan
from crown.compiler.parse_letter import parse_letter
from crown.config import REPO_ROOT, Config
from crown.data.alpaca_bars import fetch_daily_bars, fetch_minute_bars
from crown.data.calendar import market_open_close, next_trading_day, trading_days
from crown.engine.approval import AutoApprovalPolicy
from crown.engine.fills import (
    compute_proxy_shares,
    equity_entry_fill,
    settle_options_at_expiry,
    stop_exit_fill,
    target_exit_fill,
)
from crown.engine.market_data import BarReplayer
from crown.engine.orders import OrderGateway, OrderSink
from crown.engine.zonecheck import (
    EntryOutcome,
    cadence_for,
    check_entry,
    check_stop_hit,
    check_target_hit,
    ratchet_stop,
)
from crown.models import ContingentPlan, Order, ParsedLetter, PlannedTrade, Position, StopState
from crown.backtest.trade_log import TradeLog


@dataclass
class TradeRuntime:
    trade: PlannedTrade
    status: str = "watching"
    position: Position | None = None
    close_note: str | None = None


@dataclass
class BacktestResult:
    letters: list[ParsedLetter]
    plans: list[ContingentPlan]
    runtimes: dict[str, TradeRuntime]
    escalations: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)


class _EquitySink:
    """OrderSink for equity/ETF entries — the fill price and (for
    equity_proxy_pending_price trades) the actual share count are both
    resolved here, at the moment the order is submitted, since they depend
    on live price data build_plan never had."""

    def __init__(self, replayer: BarReplayer, cfg: Config):
        self.replayer = replayer
        self.cfg = cfg

    def submit(self, order: Order) -> Order:
        bar = self.replayer.bar_at(order.symbol, datetime.fromisoformat(order.created_at))
        if bar is None:
            order.status = "expired"
            order.rejection_reason = "no bar data at fill time"
            return order
        order.status = "filled"
        return order


def _regime_side(price: float, level: float) -> str:
    return "above" if price >= level else "below"


class BacktestRunner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = TradeLog(cfg.paths.trade_log_db)
        self.approval = AutoApprovalPolicy(
            cfg.backtest.approval_latency_minutes, cfg.backtest.approval_rate,
        )
        self._seq = itertools.count()
        self.escalations: list[dict] = []
        self.fills: list[dict] = []
        self._tight_symbols: set[str] = set()
        self._regime_side_seen: dict[str, str] = {}
        self._daily_realized: dict[date, float] = {}
        self._sim_now: datetime | None = None
        self._run_end: date | None = None

    # -- setup ---------------------------------------------------------

    def _load_letters(self, start: date, end: date | None) -> list[ParsedLetter]:
        letters_dir = REPO_ROOT / self.cfg.backtest.letters_dir
        pdfs = sorted(p for p in letters_dir.glob("*.pdf") if "guide" not in p.stem.lower())
        letters = [parse_letter(p, cfg=self.cfg) for p in pdfs]
        letters = [
            pl for pl in letters
            if date.fromisoformat(pl.letter_date) >= start
            and (end is None or date.fromisoformat(pl.letter_date) <= end)
        ]
        letters.sort(key=lambda pl: pl.letter_date)
        return letters

    def _all_symbols(self, plans: list[ContingentPlan]) -> list[str]:
        symbols: set[str] = set()
        for plan in plans:
            for t in plan.trades:
                if t.action == "watchlist" and t.traded_symbol:
                    symbols.add(t.traded_symbol)
        return sorted(symbols)

    # -- run -------------------------------------------------------------

    def run(
        self, start: date, end: date | None = None,
        letters: list[ParsedLetter] | None = None,
        minute_bars: dict | None = None, daily_bars: dict | None = None,
    ) -> BacktestResult:
        """`letters`/`minute_bars`/`daily_bars` are injectable so tests can
        drive the full event loop against synthetic data with no API keys
        and no network access — production callers just omit them and this
        loads real letters and fetches real Alpaca bars."""
        letters = letters if letters is not None else self._load_letters(start, end)
        plans = [build_plan(pl, self.cfg) for pl in letters]
        symbols = self._all_symbols(plans)

        run_end = end or (
            date.fromisoformat(letters[-1].letter_date) + timedelta(days=self.cfg.no_chase.default_watch_days + 30)
            if letters else start
        )
        self._run_end = run_end

        if minute_bars is None or daily_bars is None:
            if symbols:
                minute_bars = minute_bars if minute_bars is not None else fetch_minute_bars(symbols, start, run_end, self.cfg)
                daily_bars = daily_bars if daily_bars is not None else fetch_daily_bars(symbols, start, run_end, self.cfg)
            else:
                minute_bars, daily_bars = minute_bars or {}, daily_bars or {}
        replayer = BarReplayer(minute_bars, daily_bars)

        whitelist: set[str] = set()
        open_positions: dict[str, Position] = {}
        gateway = OrderGateway(
            cfg=self.cfg, sink=_EquitySink(replayer, self.cfg), whitelist=whitelist,
            open_positions_provider=lambda: list(open_positions.values()),
            daily_pnl_provider=lambda: self._current_daily_pnl(open_positions, replayer),
        )

        runtimes: dict[str, TradeRuntime] = {}
        heap: list[tuple[datetime, int, str, str]] = []  # (ts, seq, kind, trade_id)
        pending_decisions: dict[str, tuple[datetime, Order]] = {}

        def push(ts: datetime, kind: str, trade_id: str) -> None:
            heapq.heappush(heap, (ts, next(self._seq), kind, trade_id))

        for letter, plan in zip(letters, plans):
            letter_date = date.fromisoformat(letter.letter_date)
            oc = market_open_close(letter_date)
            activation_ts = oc[0] if oc else datetime.combine(letter_date, datetime.min.time(), tzinfo=timezone.utc)
            for trade in plan.trades:
                if trade.action != "watchlist":
                    self.log.log(
                        activation_ts.isoformat(), "compiled_skip",
                        letter_date=letter.letter_date, trade_id=trade.trade_id,
                        symbol=trade.traded_symbol or trade.crown_symbol,
                        detail={"reason": trade.skip_reason},
                    )
                    continue
                runtimes[trade.trade_id] = TradeRuntime(trade=trade)
                whitelist.add(trade.traded_symbol)
                push(activation_ts, "check_entry", trade.trade_id)
            self.log.log(
                activation_ts.isoformat(), "letter_activated",
                letter_date=letter.letter_date,
                detail={"trades_watching": sum(1 for t in plan.trades if t.action == "watchlist")},
            )

        for day in trading_days(start, run_end):
            oc = market_open_close(day)
            if oc:
                push(oc[1] + timedelta(minutes=1), "daily_pass", "__daily__")

        while heap:
            ts, _, kind, trade_id = heapq.heappop(heap)
            self._sim_now = ts

            if kind == "check_entry":
                self._handle_check_entry(ts, trade_id, runtimes, replayer, push, gateway, open_positions, pending_decisions)
            elif kind == "pending_decision":
                self._handle_pending_decision(ts, trade_id, runtimes, replayer, push, gateway, open_positions, pending_decisions)
            elif kind == "check_position":
                self._handle_check_position(ts, trade_id, runtimes, replayer, push, open_positions)
            elif kind == "daily_pass":
                self._handle_daily_pass(ts, runtimes, plans, replayer, open_positions)

        self.log.close()
        return BacktestResult(letters=letters, plans=plans, runtimes=runtimes,
                               escalations=self.escalations, fills=self.fills)

    # -- event handlers ----------------------------------------------------

    def _handle_check_entry(self, ts, trade_id, runtimes, replayer, push, gateway, open_positions, pending_decisions):
        rt = runtimes[trade_id]
        if rt.status != "watching":
            return
        trade = rt.trade
        sym = trade.traded_symbol
        price = replayer.price_at(sym, ts)
        if price is None:
            self._reschedule_or_expire(ts, self._base_cadence(), "check_entry", trade_id, push, rt,
                                        "still_watching_at_end")
            return

        outcome = check_entry(trade, price, ts, self.cfg)

        if outcome == EntryOutcome.TRIGGER:
            if sym in open_positions and open_positions[sym].open:
                rt.status, rt.close_note = "skipped_duplicate", (
                    f"already have an open position in {sym} from an earlier letter; not opening a duplicate"
                )
                self.log.log(ts.isoformat(), "skipped_duplicate_symbol", trade_id=trade_id, symbol=sym)
                return
            rt.status = "staged"
            order = Order(
                order_id=f"ord:{trade_id}", trade_id=trade_id, symbol=sym, side="buy",
                order_type="limit", qty=1, notional_estimate=0.0, created_at=ts.isoformat(),
            )
            decision = self.approval.decide(ts)
            pending_decisions[trade_id] = (decision.decided_at, order)
            self.log.log(ts.isoformat(), "entry_triggered", trade_id=trade_id, symbol=sym, price=price,
                          detail={"entry_zone": trade.entry_zone.model_dump()})
            push(decision.decided_at, "pending_decision", trade_id)
        elif outcome in (EntryOutcome.NO_CHASE_SKIP, EntryOutcome.WINDOW_CLOSED):
            rt.status = outcome.value
            self.log.log(ts.isoformat(), f"entry_{outcome.value}", trade_id=trade_id, symbol=sym, price=price)
        else:
            cadence = cadence_for(trade, price, self.cfg)
            self._track_tightening(sym, cadence, ts)
            next_ts = self._clamp_to_market(ts + timedelta(minutes=cadence))
            if next_ts.date() > self._run_end:
                rt.status, rt.close_note = "still_watching_at_end", "backtest range ended before this trade triggered"
                self.log.log(ts.isoformat(), "still_watching_at_end", trade_id=trade_id, symbol=sym)
            else:
                push(next_ts, "check_entry", trade_id)

    def _handle_pending_decision(self, ts, trade_id, runtimes, replayer, push, gateway, open_positions, pending_decisions):
        rt = runtimes[trade_id]
        decided_at, order = pending_decisions.pop(trade_id, (None, None))
        if order is None:
            return
        trade = rt.trade
        sym = order.symbol
        price = replayer.price_at(sym, ts) or 0.0

        # Re-check the duplicate-symbol dedup here, not just at trigger time:
        # two trades on the same symbol from the same letter can both
        # trigger before either has actually filled (they share the same
        # approval latency), so the trigger-time check alone isn't enough —
        # this is the definitive check, right before a position is opened.
        if sym in open_positions and open_positions[sym].open:
            rt.status, rt.close_note = "skipped_duplicate", (
                f"another trade for {sym} filled first during the same approval window; not opening a duplicate"
            )
            self.log.log(ts.isoformat(), "skipped_duplicate_symbol", trade_id=trade_id, symbol=sym)
            return

        # Latency already elapsed to reach this event (it was baked into
        # decided_at when the order was staged) — only the approve/reject
        # draw is decided now.
        if not self._approval_draw():
            rt.status, rt.close_note = "rejected_approval", "not approved by the simulated approval policy"
            self.log.log(ts.isoformat(), "approval_rejected", trade_id=trade_id, symbol=sym)
            return

        is_options = trade.asset_class == "options_spread"
        if is_options:
            contracts = trade.size.contracts or 0
            if contracts < 1:
                rt.status, rt.close_note = "skipped", "resolved to 0 contracts at fill time"
                return
            notional = (trade.max_risk_per_unit or 0.0) * contracts
        else:
            if trade.size.method == "equity_proxy_pending_price":
                stop_ref = trade.stop.current
                shares, stop_level = compute_proxy_shares(
                    trade.size.risk_dollars, price, stop_ref, trade.direction,
                    self.cfg.alternative_finder.default_proxy_stop_pct,
                )
            else:
                shares = trade.size.shares or 0
                stop_level = trade.stop.current
            if shares < 1:
                rt.status, rt.close_note = "skipped", "resolved to 0 shares at fill time"
                return
            notional = shares * price

        order.qty = contracts if is_options else shares
        order.notional_estimate = notional
        order = gateway.submit(order)

        if order.status != "filled":
            rt.status, rt.close_note = "rejected_guardrail", order.rejection_reason
            self.log.log(ts.isoformat(), "guardrail_rejected", trade_id=trade_id, symbol=sym,
                          detail={"reason": order.rejection_reason})
            return

        if is_options:
            rt.status = "open"
            rt.position = Position(
                trade_id=trade_id, symbol=sym, direction=trade.direction, qty=contracts,
                entry_price=trade.credit_or_debit or 0.0, entry_at=ts.isoformat(),
                stop=trade.stop.model_copy(), targets=[t.model_copy() for t in trade.targets],
            )
            open_positions[sym] = rt.position
            self.log.log(ts.isoformat(), "options_entered", trade_id=trade_id, symbol=sym,
                          qty=contracts, price=trade.credit_or_debit,
                          detail={"expiry": trade.expiry, "structure": trade.structure})
            self.fills.append({"trade_id": trade_id, "symbol": sym, "side": "options_entry",
                                "qty": contracts, "price": trade.credit_or_debit, "ts": ts.isoformat()})
        else:
            entry_fill = price
            stop = trade.stop.model_copy()
            if trade.size.method == "equity_proxy_pending_price":
                stop.current = stop_level
                stop.initial = stop_level
            rt.status = "open"
            rt.position = Position(
                trade_id=trade_id, symbol=sym, direction=trade.direction, qty=shares,
                entry_price=entry_fill, entry_at=ts.isoformat(),
                stop=stop, targets=[t.model_copy() for t in trade.targets],
            )
            open_positions[sym] = rt.position
            self.log.log(ts.isoformat(), "equity_entered", trade_id=trade_id, symbol=sym,
                          qty=shares, price=entry_fill, detail={"stop": stop.current})
            self.fills.append({"trade_id": trade_id, "symbol": sym, "side": "buy" if trade.direction == "long" else "sell_short",
                                "qty": shares, "price": entry_fill, "ts": ts.isoformat()})
            push(ts, "check_position", trade_id)

    def _approval_draw(self) -> bool:
        return self.approval._rng.random() < self.approval.approve_rate

    def _handle_check_position(self, ts, trade_id, runtimes, replayer, push, open_positions):
        rt = runtimes[trade_id]
        if rt.status != "open" or rt.trade.asset_class == "options_spread":
            return
        pos = rt.position
        price = replayer.price_at(pos.symbol, ts)
        if price is None:
            self._reschedule_or_expire(ts, 60, "check_position", trade_id, push, rt, "open")
            return

        if check_stop_hit(pos, price):
            self._close_equity_position(ts, rt, pos, price, "stop_hit", open_positions)
            return

        target = check_target_hit(pos, price)
        if target is not None:
            target.hit = True
            half_qty = pos.qty / 2 if rt.trade.half_off_at_t1 and target.tier == "T1" else pos.qty
            exit_price = target_exit_fill(target.level)
            pnl = self._pnl(pos, exit_price, half_qty)
            pos.realized_pnl += pnl
            self._record_daily_realized(ts.date(), pnl)
            self.log.log(ts.isoformat(), "target_hit", trade_id=trade_id, symbol=pos.symbol,
                          price=exit_price, qty=half_qty, pnl=pnl, detail={"tier": target.tier})
            self.fills.append({"trade_id": trade_id, "symbol": pos.symbol, "side": "trim",
                                "qty": half_qty, "price": exit_price, "ts": ts.isoformat(), "pnl": pnl})
            pos.qty -= half_qty
            if pos.qty <= 0:
                pos.open = False
                open_positions.pop(pos.symbol, None)
                rt.status, rt.close_note = "closed", f"{target.tier} hit, fully closed"
                return
            breakeven = pos.entry_price
            ratchet_stop(pos, breakeven)

        self._reschedule_or_expire(ts, self._base_cadence(), "check_position", trade_id, push, rt, "open")

    def _close_equity_position(self, ts, rt, pos, price, reason, open_positions):
        exit_price = stop_exit_fill(
            {"open": price, "high": price, "low": price}, pos.stop.current, pos.direction,
        )
        pnl = self._pnl(pos, exit_price, pos.qty)
        pos.realized_pnl += pnl
        pos.open = False
        pos.closed_at = ts.isoformat()
        pos.close_reason = reason
        open_positions.pop(pos.symbol, None)
        rt.status, rt.close_note = "closed", reason
        self._record_daily_realized(ts.date(), pnl)
        self.log.log(ts.isoformat(), "position_closed", trade_id=rt.trade.trade_id, symbol=pos.symbol,
                      price=exit_price, qty=pos.qty, pnl=pnl, detail={"reason": reason})
        self.fills.append({"trade_id": rt.trade.trade_id, "symbol": pos.symbol, "side": "close",
                            "qty": pos.qty, "price": exit_price, "ts": ts.isoformat(), "pnl": pnl})

    def _pnl(self, pos: Position, exit_price: float, qty: float) -> float:
        if pos.direction == "long":
            return (exit_price - pos.entry_price) * qty
        return (pos.entry_price - exit_price) * qty

    def _handle_daily_pass(self, ts, runtimes, plans, replayer, open_positions):
        day = ts.date()

        for rt in runtimes.values():
            trade = rt.trade
            if rt.status != "open":
                continue

            if trade.asset_class == "options_spread":
                pos = rt.position
                close = replayer.daily_close(pos.symbol, day)
                if close is None:
                    continue
                breached = self._behavioral_breach(pos, close, day)
                expiry_due = trade.expiry and day >= date.fromisoformat(trade.expiry)
                if breached:
                    pnl = -(trade.max_risk_per_unit or 0.0) * pos.qty
                    pos.realized_pnl += pnl
                    pos.open = False
                    pos.closed_at = ts.isoformat()
                    pos.close_reason = "behavioral_stop_breached"
                    open_positions.pop(pos.symbol, None)
                    rt.status, rt.close_note = "closed", "behavioral stop breached (approximate: full max-risk loss booked)"
                    self._record_daily_realized(day, pnl)
                    self.log.log(ts.isoformat(), "options_stop_breached", trade_id=trade.trade_id,
                                  symbol=pos.symbol, price=close, pnl=pnl)
                elif expiry_due:
                    settled = settle_options_at_expiry(trade, close, pos.qty)
                    if settled is None:
                        pnl = (trade.credit_or_debit or 0.0) * 100 * pos.qty
                        note = "held to expiry, approximate settlement: assumed full credit/debit capture (no strikes to compute intrinsic value)"
                    else:
                        pnl = settled
                        note = "held to expiry, exact intrinsic-value settlement"
                    pos.realized_pnl += pnl
                    pos.open = False
                    pos.closed_at = ts.isoformat()
                    pos.close_reason = note
                    open_positions.pop(pos.symbol, None)
                    rt.status, rt.close_note = "closed", note
                    self._record_daily_realized(day, pnl)
                    self.log.log(ts.isoformat(), "options_settled", trade_id=trade.trade_id,
                                  symbol=pos.symbol, price=close, pnl=pnl, detail={"note": note})

        for plan in plans:
            for zone in plan.watch_zones:
                if zone.kind != "regime":
                    continue
                close = replayer.daily_close(zone.symbol, day)
                if close is None:
                    continue
                side = _regime_side(close, zone.level)
                key = f"{zone.symbol}:{zone.level}:{zone.meaning}"
                last = self._regime_side_seen.get(key)
                if last is not None and last != side:
                    self.escalations.append({
                        "ts": ts.isoformat(), "type": "regime_crossing", "symbol": zone.symbol,
                        "level": zone.level, "meaning": zone.meaning, "new_side": side,
                    })
                    self.log.log(ts.isoformat(), "regime_crossing", symbol=zone.symbol,
                                  detail={"level": zone.level, "meaning": zone.meaning, "side": side})
                self._regime_side_seen[key] = side

    def _behavioral_breach(self, pos: Position, close: float, day: date) -> bool:
        stop = pos.stop
        if stop.type != "behavioral" or stop.current is None:
            return False
        breached_today = (
            (pos.direction == "short" and close >= stop.current) or
            (pos.direction == "long" and close <= stop.current)
        )
        if not breached_today:
            stop.behavioral_sessions_count = 0
            return False
        stop.behavioral_sessions_count += 1
        required = stop.behavioral_sessions_required or 1
        return stop.behavioral_sessions_count >= required

    def _current_daily_pnl(self, open_positions: dict[str, Position], replayer: BarReplayer) -> float:
        # Uses simulated time, not wall-clock — this is a backtest, and the
        # daily-loss-limit guardrail must key off the day being replayed,
        # not the day the code happens to actually run.
        now = self._sim_now or datetime.now(timezone.utc)
        today = now.date()
        realized = self._daily_realized.get(today, 0.0)
        unrealized = 0.0
        for pos in open_positions.values():
            price = replayer.price_at(pos.symbol, now)
            if price is None:
                continue
            unrealized += (price - pos.entry_price) * pos.qty if pos.direction == "long" \
                else (pos.entry_price - price) * pos.qty
        return realized + unrealized

    def _record_daily_realized(self, day: date, pnl: float) -> None:
        self._daily_realized[day] = self._daily_realized.get(day, 0.0) + pnl

    def _base_cadence(self) -> int:
        return max(t.cadence_minutes for t in self.cfg.scheduling.tiers)

    def _reschedule_or_expire(self, ts, delta_minutes, kind, trade_id, push, rt, expire_status) -> None:
        """Shared guard for every 'check again later' reschedule: once the
        next check would fall past the backtest's run_end, stop rescheduling
        instead of pushing forever — this is what makes the event queue
        provably terminate for a trade that never triggers or a position
        that never hits its stop/target within the replayed window.
        `expire_status="open"` means "leave the status as-is (it's an open
        position that just never resolved), only stop rescheduling and note
        why" rather than actually overwriting the status."""
        next_ts = ts + timedelta(minutes=delta_minutes)
        if next_ts.date() > self._run_end:
            if expire_status != "open":
                rt.status = expire_status
            rt.close_note = rt.close_note or "backtest range ended before this resolved"
            self.log.log(ts.isoformat(), f"expired_{expire_status}", trade_id=trade_id)
        else:
            push(next_ts, kind, trade_id)

    def _track_tightening(self, symbol: str, cadence: int, ts: datetime) -> None:
        base = self._base_cadence()
        is_tight = cadence < base
        cap = self.cfg.scheduling.max_concurrent_tight_jobs
        if is_tight:
            if symbol not in self._tight_symbols and len(self._tight_symbols) >= cap:
                self.escalations.append({
                    "ts": ts.isoformat(), "type": "tighten_capped", "symbol": symbol,
                    "note": f"wanted to tighten to {cadence}min but {cap} concurrent tight jobs already active",
                })
                return
            if symbol not in self._tight_symbols:
                self.escalations.append({
                    "ts": ts.isoformat(), "type": "tighten", "symbol": symbol, "cadence_minutes": cadence,
                })
            self._tight_symbols.add(symbol)
        elif symbol in self._tight_symbols:
            self._tight_symbols.discard(symbol)
            self.escalations.append({"ts": ts.isoformat(), "type": "untighten", "symbol": symbol})

    def _clamp_to_market(self, ts: datetime) -> datetime:
        oc = market_open_close(ts.date())
        if oc and oc[0] <= ts <= oc[1]:
            return ts
        nxt = next_trading_day(ts.date())
        noc = market_open_close(nxt)
        return noc[0] if noc else ts
