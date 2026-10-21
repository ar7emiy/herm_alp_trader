"""Closed-daily-bar replay. Never calls an LLM, network, or order adapter."""
from __future__ import annotations

from collections import Counter

from crown.data.calendar import market_open_close, trading_days
from crown.research.data import prior_atr
from crown.research.gateway import Gateway
from crown.research.journal import Journal
from crown.research.models import D, digest, serial


def run(batches, bars, policy, start, end, manifest_hash, journal_path=None):
    journal=Journal(journal_path)
    g=Gateway(policy,journal,manifest_hash)
    pending=list(sorted(batches,key=lambda b:b.available_at))
    curve=[]
    eligible={}
    prior_closes={}
    latest_letter_date=""
    day_equities=[policy.initial_cash]
    peak=policy.initial_cash
    max_drawdown=D(0)
    slip=policy.slippage_bps/D(10000)
    try:
        for day in trading_days(start,end):
            opened,closed=market_open_close(day)
            g.settle(opened)
            # Only each bar's open is observable at this point.
            today={s:series[day] for s,series in bars.items() if day in series}
            for s,bar in today.items():
                if s in prior_closes and abs(bar.open/prior_closes[s]-1)>D('.4'):
                    raise RuntimeError(f"Large raw-price discontinuity for {s} on {day}; corporate-action verification required, no valid return reported")
            for h in g.state.holdings.values():
                if h.plan.symbol in today:
                    h.mark=today[h.plan.symbol].open
            if g.state.equity-day_equities[-1]<=-policy.daily_loss_limit or g.state.equity-day_equities[max(0,len(day_equities)-5)]<=-policy.weekly_loss_limit:
                g.halt(opened,"Opening gap breached loss cap; block new exposure before entries")
            while pending and pending[0].available_at<=opened:
                batch=pending.pop(0)
                if batch.letter_date<latest_letter_date:
                    g.record(opened,"stale_letter","Late older letter cannot replace newer evidence or reactivate an episode",
                             letter_date=batch.letter_date,latest_letter_date=latest_letter_date)
                    continue
                latest_letter_date=batch.letter_date
                g.record(opened,"letter_received",batch.timing_assumption,letter_date=batch.letter_date,
                         available_at=batch.available_at,scope="Independently corroborated research, not approved paper")
                seen={p.position_id for p in batch.plans}
                for pid,h in list(g.state.holdings.items()):
                    if pid in batch.closures:
                        h.pending_exit="Crown explicitly closed this identical long episode"
                    elif pid not in seen:
                        h.pending_exit="Missing from new letter; thesis not presumed valid (conservative research exit)"
                for plan in batch.plans:
                    eligible[plan.position_id]=plan
                    if not plan.allowed:
                        g.record(opened,"skip","; ".join(plan.reasons),plan)
                    if plan.position_id in g.state.holdings:
                        h=g.state.holdings[plan.position_id]
                        if not plan.allowed:
                            h.pending_exit="New letter no longer supports a fully monitorable executable plan"
                        else:
                            old_stop=h.stop
                            h.plan=plan
                            if plan.stop is not None:
                                g.tighten(h.position_id,plan.stop,opened,"Crown stop update; loosening prohibited")
                                if plan.stop>=old_stop and plan.stop_kind=="touch":
                                    h.protection_kind="touch"
            # Mark evidence age, execute queued close-only exits on the next open.
            for pid,h in list(g.state.holdings.items()):
                if day>h.plan.expires_on:
                    h.pending_exit="Source evidence expired; do not carry a thesis between missing letters"
                bar=today.get(h.plan.symbol)
                if not bar:
                    g.record(opened,"data_missing","No executable bar; entry frozen and exit remains pending",h.plan)
                    h.pending_exit=h.pending_exit or "Exit after data outage; protection could not be simulated during gap"
                    continue
                if h.pending_exit:
                    g.sell(pid,h.quantity,bar.open*(1-slip),opened,h.pending_exit)
            # One attempted entry per source plan. A rejected setup requires a new
            # letter; no repeated chasing of the same historical opportunity.
            for pid,plan in list(eligible.items()):
                if not plan.allowed or pid in g.state.holdings or pid in g.entered_episodes:
                    continue
                bar=today.get(plan.symbol)
                if not bar:
                    g.record(opened,"skip","No market-data coverage for executable entry",plan)
                else:
                    reservation=g.reserve(plan,bar.open,prior_atr(bars[plan.symbol],day,policy.atr_sessions),opened)
                    if reservation:
                        g.fill_buy(reservation,plan,reservation.limit,reservation.quantity,opened)
                del eligible[pid]
            # Daily range ordering is unknowable. A touched stop always precedes
            # targets. Close-only signals never obtain that same closing fill.
            for pid,h in list(g.state.holdings.items()):
                bar=today.get(h.plan.symbol)
                if not bar:
                    continue
                if h.protection_kind=="touch" and bar.low<=h.stop:
                    g.sell(pid,h.quantity,min(bar.open,h.stop)*(1-slip),closed,
                           "Protective touch stop; gap-through honored; stop wins same-bar ambiguity")
                    continue
                if h.protection_kind=="close":
                    h.close_breaches=h.close_breaches+1 if bar.close<h.stop else 0
                    if h.close_breaches>=h.plan.stop_sessions:
                        h.pending_exit="Confirmed closing-price stop; exit at next available open"
                invalidated=False
                for i,(op,level,sessions) in enumerate(h.plan.invalidation):
                    if op=="trade_below" and bar.low<=level:
                        g.sell(pid,h.quantity,min(bar.open,level)*(1-slip),closed,"Crown underlying thesis invalidation")
                        invalidated=True
                        break
                    if op=="close_below":
                        h.invalidation_breaches[i]=h.invalidation_breaches.get(i,0)+1 if bar.close<level else 0
                        if h.invalidation_breaches[i]>=sessions:
                            h.pending_exit="Crown close-based thesis invalidation; next available open"
                if invalidated:
                    continue
                # Never assume a favorable intraday sequence after an adverse
                # closing signal. Skip targets if an exit signal is pending.
                if not h.pending_exit and h.target_index<len(h.plan.targets):
                    target=h.plan.targets[h.target_index]
                    if bar.high>=target:
                        last=h.target_index>0
                        trim=h.quantity if last else (h.quantity/D(2)//policy.share_step)*policy.share_step
                        if trim<=0:
                            trim=h.quantity
                            g.record(closed,"granularity_deviation","Cannot halve minimum-sized position; close whole position",h.plan)
                        g.sell(pid,trim,target*(1-slip),closed,"Crown scalar target; fixed base-guide trim policy")
                        if pid not in g.state.holdings:
                            continue
                        h=g.state.holdings[pid]
                        h.target_index+=1
                        if h.entry_price>h.stop:
                            g.tighten(pid,h.entry_price,closed,"After T1 tighten to entry for next session")
                h=g.state.holdings.get(pid)
                if h is None:
                    continue
                h.mark=bar.close
                h.best_close=max(h.best_close,bar.close)
                if policy.atr_protection and bar.close>=h.entry_price+(h.entry_price-h.initial_stop):
                    # Include this completed close in ATR only for tomorrow's stop.
                    from crown.data.calendar import next_trading_day
                    atr=prior_atr(bars[h.plan.symbol],next_trading_day(day),policy.atr_sessions)
                    if atr:
                        trail=h.best_close-policy.trail_multiple*atr
                        if trail>h.stop:
                            g.tighten(pid,trail,closed,f"{policy.trail_multiple} ATR trail from known closes; effective next session")
                            h.protection_kind="touch"
            value=g.state.equity
            peak=max(peak,value)
            max_drawdown=max(max_drawdown,(peak-value)/peak)
            if value-day_equities[-1]<=-policy.daily_loss_limit or value-day_equities[max(0,len(day_equities)-5)]<=-policy.weekly_loss_limit:
                g.halt(closed,"Daily or rolling-five-session loss cap; new exposure disabled, exits continue")
            curve.append({"day":day,"equity":value,"settled_cash":g.state.settled_cash,
                          "unsettled_cash":sum((v for _,v in g.state.unsettled),D(0)),
                          "open_risk_estimate":g.state.open_risk,"positions":len(g.state.holdings)})
            day_equities.append(value)
            prior_closes.update({s:b.close for s,b in today.items()})
        events=journal.events
        counts=Counter(e["kind"] for e in events)
        return serial({"policy":policy,"manifest_sha256":manifest_hash,"start":start,"end":end,
                       "initial_equity":policy.initial_cash,"final_equity":g.state.equity,
                       "net_pnl":g.state.equity-policy.initial_cash,"realized_pnl":g.state.realized_pnl,
                       "return_pct":(g.state.equity/policy.initial_cash-1)*100,
                       "max_drawdown_pct":max_drawdown*100,"halted":g.halted,
                       "counts":dict(counts),"open_positions":list(g.state.holdings.values()),
                       "settled_cash":g.state.settled_cash,"unsettled_cash":g.state.unsettled,
                       "equity_curve":curve,"events":events,"journal_sha256":journal.head,
                       "future_letters_not_used":[b.letter_date for b in pending]})
    finally:
        journal.close()
