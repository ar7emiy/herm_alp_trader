"""Cash-only, long-only portfolio gateway for the shared research domain.

There is deliberately no HTTP/broker client here. A future paper adapter must
pass separate endpoint, execution, protection and reconciliation acceptance gates.
"""
from __future__ import annotations

import copy
from datetime import datetime
from decimal import Decimal, ROUND_CEILING

from crown.data.calendar import next_funding_day
from crown.research.journal import Journal
from crown.research.models import Holding, Plan, Portfolio, Reservation, ResearchPolicy, digest

ZERO = Decimal("0")


def fee(notional: Decimal, bps: Decimal) -> Decimal:
    return (notional * bps / Decimal("10000")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


class Gateway:
    def __init__(self, policy: ResearchPolicy, journal: Journal, manifest_hash: str):
        self.policy = policy
        self.journal = journal
        self.manifest_hash = manifest_hash
        self.policy_hash = digest(policy)
        self.state = Portfolio(policy.initial_cash)
        self.halted = False
        self.entered_episodes: set[str] = set()

    def record(self, at: datetime, kind: str, reason: str, plan: Plan | None = None, **details):
        if at.tzinfo is None:
            raise ValueError("Decisions require timezone-aware timestamps")
        inputs = {"manifest_sha256": self.manifest_hash, "mode": "historical_research"}
        if plan is not None:
            source_complete=bool(plan.source.get("quote") and plan.source.get("page"))
            if not source_complete and kind!="skip":
                raise ValueError("Missing Crown source precludes a decision")
            inputs.update(record_sha256=plan.record_sha256, crown_position_key=plan.crown_key,
                          crown_source=plan.source, available_at=plan.available_at,
                          deviations=plan.deviations)
            inputs["source_complete"]=source_complete
            details.update(position_id=plan.position_id, symbol=plan.symbol)
        return self.journal.append(at=at, kind=kind, reason=reason, policy_hash=self.policy_hash, inputs=inputs, **details)

    def settle(self, at: datetime):
        day = at.date()
        matured = [(d,v) for d,v in self.state.unsettled if d <= day]
        if matured:
            self.record(at, "cash_settlement", "Conservative research funding calendar", amount=sum((v for _,v in matured),ZERO))
            self.state.settled_cash += sum((v for _,v in matured),ZERO)
            self.state.unsettled = [(d,v) for d,v in self.state.unsettled if d > day]

    def reserve(self, plan: Plan, open_price: Decimal, prior_atr: Decimal | None, at: datetime,
                approved: bool = True) -> Reservation | None:
        p, state = self.policy, self.state
        intent_id=digest((plan.position_id,plan.record_sha256,at,"buy"))[:24]
        if intent_id in state.reservations:
            return state.reservations[intent_id]
        reasons = list(plan.reasons)
        if not plan.source.get("quote") or not plan.source.get("page"):
            reasons.append("Missing Crown source blocks execution; only a skip can be recorded")
        if not plan.allowed:
            reasons.append("Plan is blocked")
        if self.halted:
            reasons.append("HALT blocks new exposure")
        if not approved:
            reasons.append("Simulated approval denied")
        if at < plan.available_at or at.date() > plan.expires_on:
            reasons.append("Outside information/entry window")
        if plan.position_id in self.entered_episodes:
            reasons.append("This Crown episode already traded; no automatic re-entry")
        if any(r.position_id==plan.position_id for r in state.reservations.values()):
            reasons.append("A pending intent already reserves this Crown episode")
        if any(h.plan.symbol == plan.symbol for h in state.holdings.values()):
            reasons.append("Existing symbol exposure; no independent duplicate entry")
        if open_price <= 0 or not open_price.is_finite():
            reasons.append("Invalid executable price")
        if plan.anchor is not None and abs(open_price / plan.anchor - 1) > p.max_chase:
            reasons.append("Published entry no-chase bound exceeded")
        if plan.anchor is None and not p.allow_unanchored_holdings:
            reasons.append("No published underlying entry for no-chase check")
        if any(target<=open_price for target in plan.targets):
            reasons.append("Published target already reached before our entry; no invented continuation")
        if not ZERO < plan.multiplier <= 1:
            reasons.append("Risk multiplier outside supported tiers")
        occupied = set(state.holdings) | {r.position_id for r in state.reservations.values()}
        if len(occupied) >= p.max_positions and plan.position_id not in occupied:
            reasons.append("Concurrent-position cap")
        if reasons:
            self.record(at,"skip","; ".join(reasons),plan)
            return None
        limit = (open_price * (1 + p.slippage_bps / Decimal("10000"))).quantize(Decimal("0.0001"))
        stop, protection_kind = plan.stop, plan.stop_kind
        if p.atr_protection:
            if prior_atr is None or prior_atr <= 0:
                self.record(at,"skip","ATR warmup/data missing; no fallback protection",plan)
                return None
            atr_stop = limit - p.atr_multiple * prior_atr
            if stop is None or atr_stop > stop:
                stop, protection_kind = atr_stop, "touch"
            if protection_kind not in {"touch","close"}:
                # Research may conservatively interpret an ambiguous convention
                # as touch, but must NEVER lower the printed numerical stop.
                protection_kind="touch"
        if stop is None or stop <= 0 or stop >= open_price or protection_kind not in {"touch","close"}:
            self.record(at,"skip","No executable protective stop below our entry",plan)
            return None
        distance = limit-stop
        cash_room = state.settled_cash-state.reserved_cash-p.initial_cash*p.reserve_fraction
        thesis_risk = sum((h.risk for h in state.holdings.values() if h.plan.thesis_id==plan.thesis_id),ZERO)
        thesis_risk += sum((r.risk for r in state.reservations.values() if r.thesis_id==plan.thesis_id),ZERO)
        risk_room = min(p.risk_unit*plan.multiplier,p.max_open_risk-state.open_risk,
                        p.max_cluster_risk-state.open_risk,p.max_thesis_risk-thesis_risk)
        # All supported assets share one conservative cluster in this first study.
        room = min(risk_room/distance, p.max_order_notional/limit,
                   max(ZERO,cash_room-Decimal("0.01"))/(limit*(1+p.fee_bps/Decimal("10000"))))
        quantity = max(ZERO,(room//p.share_step)*p.share_step)
        if quantity <= 0:
            self.record(at,"skip","Rounds to zero within settled cash, reserve, risk and notional caps",plan)
            return None
        cash = quantity*limit + fee(quantity*limit,p.fee_bps)
        reservation=Reservation(intent_id,plan.position_id,plan.thesis_id,plan.symbol,quantity,limit,stop,protection_kind,cash,quantity*distance)
        self.record(at,"entry_intent","Bounded cash-only research entry with explicit protection",plan,
                    intent_id=intent_id,quantity=quantity,limit=limit,stop=stop,protection_kind=protection_kind,
                    risk_budget=p.risk_unit*plan.multiplier,planned_risk=reservation.risk,cash_reserved=cash,
                    fractional=quantity!=quantity.to_integral_value(),cluster_model="single conservative cluster")
        state.reservations[intent_id]=reservation
        return reservation

    def fill_buy(self, reservation: Reservation, plan: Plan, price: Decimal, quantity: Decimal, at: datetime,
                 fill_id: str | None = None) -> bool:
        fill_id=fill_id or f"{reservation.intent_id}:fill"
        if fill_id in self.state.applied_fills:
            return False
        r=self.state.reservations.get(reservation.intent_id)
        if (r is None or r.position_id!=plan.position_id or r.symbol!=plan.symbol or r.thesis_id!=plan.thesis_id
                or at<plan.available_at or price<=r.stop or price>r.limit or quantity<=0 or quantity>r.quantity):
            raise ValueError("Fill violates its reserved order bounds")
        charge=price*quantity+fee(price*quantity,self.policy.fee_bps)
        if charge>r.cash or charge>self.state.settled_cash:
            raise ValueError("Fill would overspend reserved/settled cash")
        state=copy.deepcopy(self.state)
        state.settled_cash-=charge
        remaining=r.quantity-quantity
        if remaining:
            from dataclasses import replace
            state.reservations[r.intent_id]=replace(r,quantity=remaining,
                cash=remaining*r.limit+fee(remaining*r.limit,self.policy.fee_bps),
                risk=remaining*(r.limit-r.stop))
        else:
            del state.reservations[r.intent_id]
        if state.settled_cash-state.reserved_cash < self.policy.initial_cash*self.policy.reserve_fraction:
            raise ValueError("Partial-fill costs would consume another reservation or cash reserve")
        if plan.position_id in state.holdings:
            h=state.holdings[plan.position_id]
            h.entry_price=(h.entry_price*h.quantity+price*quantity)/(h.quantity+quantity)
            h.quantity+=quantity; h.cost_basis+=charge
        else:
            state.holdings[plan.position_id]=Holding(
                position_id=plan.position_id,plan=plan,quantity=quantity,cost_basis=charge,
                entry_price=price,initial_stop=r.stop,stop=r.stop,
                protection_kind=r.protection_kind,mark=price,entered_at=at,best_close=price)
        state.applied_fills.add(fill_id)
        self.record(at,"entry_fill","Simulated fill; no broker order was submitted",plan,
                    fill_id=fill_id,intent_id=r.intent_id,price=price,quantity=quantity,cost=charge,
                    fee=fee(price*quantity,self.policy.fee_bps))
        self.state=state
        self.entered_episodes.add(plan.position_id)
        return True

    def cancel(self,intent_id: str,at: datetime):
        if intent_id in self.state.reservations:
            self.record(at,"cancel_intent","Release an unfilled research reservation",intent_id=intent_id)
            del self.state.reservations[intent_id]

    def sell(self,position_id: str,quantity: Decimal,price: Decimal,at: datetime,reason: str,fill_id: str | None=None):
        fill_id=fill_id or digest((position_id,at,reason,quantity))[:24]
        if fill_id in self.state.applied_fills:
            return ZERO
        if position_id not in self.state.holdings or quantity<=0 or quantity>self.state.holdings[position_id].quantity or price<=0:
            raise ValueError("Sale must reduce an existing long; no shorting")
        state=copy.deepcopy(self.state); h=state.holdings[position_id]
        basis=h.cost_basis*quantity/h.quantity
        proceeds=quantity*price-fee(quantity*price,self.policy.fee_bps)
        pnl=proceeds-basis
        state.unsettled.append((next_funding_day(at.date()),proceeds))
        state.realized_pnl+=pnl; h.realized_pnl+=pnl
        h.quantity-=quantity; h.cost_basis-=basis
        if h.quantity==0:
            del state.holdings[position_id]
        state.applied_fills.add(fill_id)
        self.record(at,"exit_fill",reason,h.plan,fill_id=fill_id,quantity=quantity,price=price,
                    realized_pnl=pnl,proceeds_unsettled=proceeds,funding_day=next_funding_day(at.date()),
                    fee=fee(price*quantity,self.policy.fee_bps))
        self.state=state
        return pnl

    def tighten(self,position_id: str,stop: Decimal,at: datetime,reason: str):
        h=self.state.holdings[position_id]
        if stop<h.stop:
            self.record(at,"stop_rejected","Stop ratchet rejected a loosening",h.plan,proposed=stop,current=h.stop)
            return False
        if stop<=0:
            raise ValueError("Stop must be positive")
        self.record(at,"stop_change",reason,h.plan,old_stop=h.stop,new_stop=stop)
        h.stop=stop
        return True

    def halt(self,at: datetime,reason: str):
        if not self.halted:
            self.record(at,"halt",reason)
            self.halted=True
        for intent in list(self.state.reservations):
            self.cancel(intent,at)
