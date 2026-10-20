from dataclasses import replace
from datetime import date, datetime, timezone
import sqlite3

import pytest

from crown.research.gateway import Gateway
from crown.research.journal import Journal
from crown.research.models import D, Plan, ResearchPolicy

AT=datetime(2026,6,1,14,tzinfo=timezone.utc)


def plan(**changes):
    base=Plan("episode","thesis","ETF",AT,date(2026,6,30),"2026-06-01","a"*64,"crown-position",
              {"page":2,"quote":"Long ETF. Entry 100. Stop 95 touch."},D(100),D(1),D(95),"touch",1,())
    return replace(base,**changes)


def gateway(policy=None):
    return Gateway(policy or ResearchPolicy(),Journal(),"b"*64)


def test_reservation_partial_fill_duplicate_and_settlement():
    g=gateway()
    p=plan()
    r=g.reserve(p,D(100),None,AT)
    assert g.reserve(p,D(100),None,AT)==r
    assert r.quantity==D(14)
    g.fill_buy(r,p,r.limit,D(7),AT,fill_id="first")
    assert not g.fill_buy(r,p,r.limit,D(7),AT,fill_id="first")
    assert g.state.reservations[r.intent_id].quantity==7
    g.fill_buy(r,p,r.limit,D(7),AT,fill_id="second")
    assert not g.state.reservations
    assert g.state.holdings[p.position_id].quantity==14
    assert g.state.settled_cash>=500
    before=g.state.settled_cash
    g.sell(p.position_id,D(14),D(110),AT,"target")
    assert g.state.settled_cash==before
    assert g.state.unsettled
    g.settle(AT.replace(day=2))
    assert not g.state.unsettled and g.state.settled_cash>5000
    assert g.reserve(p,D(100),None,AT.replace(day=3)) is None


def test_pending_orders_share_cash_and_risk_caps():
    g=gateway(ResearchPolicy(initial_cash=D(1000),max_cluster_risk=D(1000),max_open_risk=D(1000)))
    r=g.reserve(plan(),D(100),None,AT)
    assert r and r.cash<=900
    second=g.reserve(plan(position_id="second",thesis_id="second",symbol="OTHER"),D(100),None,AT)
    assert second is None
    assert g.state.reserved_cash<=900


def test_no_journal_no_trade_and_bounds(monkeypatch):
    g=gateway()
    r=g.reserve(plan(),D(100),None,AT)
    before=g.state.settled_cash
    with pytest.raises(ValueError):g.fill_buy(r,plan(),r.limit+D(1),r.quantity,AT)
    with pytest.raises(ValueError):g.fill_buy(r,plan(symbol="WRONG"),r.limit,r.quantity,AT)
    monkeypatch.setattr(g.journal,"append",lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):g.fill_buy(r,plan(),r.limit,r.quantity,AT)
    assert not g.state.holdings and g.state.settled_cash==before


def test_halt_allows_exits_but_ratchet_never_loosens():
    g=gateway()
    r=g.reserve(plan(),D(100),None,AT)
    g.fill_buy(r,plan(),r.limit,r.quantity,AT)
    assert g.tighten("episode",D(98),AT,"tighten")
    assert not g.tighten("episode",D(90),AT,"loosen")
    assert g.state.holdings["episode"].stop==98
    g.halt(AT,"owner halt")
    assert g.reserve(plan(position_id="new",symbol="NEW"),D(100),None,AT) is None
    with pytest.raises(ValueError):g.sell("episode",r.quantity+1,D(100),AT,"oversell")
    g.sell("episode",r.quantity,D(100),AT,"risk reduction")
    assert not g.state.holdings


def test_atr_protection_is_carried_into_fill():
    g=gateway(ResearchPolicy.protective_research())
    p=plan(stop=None,stop_kind="ambiguous")
    r=g.reserve(p,D(100),D(2),AT)
    g.fill_buy(r,p,r.limit,r.quantity,AT)
    assert g.state.holdings["episode"].protection_kind=="touch"
    assert g.state.holdings["episode"].stop==r.limit-4


def test_atr_never_lowers_even_an_ambiguous_printed_stop():
    g=gateway(ResearchPolicy.protective_research())
    p=plan(stop=D(98),stop_kind="ambiguous")
    r=g.reserve(p,D(100),D(5),AT)
    assert r.stop==98 and r.protection_kind=="touch"


def test_durable_journal_is_append_only_and_deterministic(tmp_path):
    j=Journal(tmp_path/"events.sqlite")
    j.append(at=AT,kind="test",reason="explain",policy_hash="a",inputs={"source":"b"})
    with pytest.raises(sqlite3.IntegrityError):j.connection.execute("DELETE FROM events")
    h=j.head
    j.close()
    with pytest.raises(ValueError):Journal(tmp_path/"events.sqlite")
    other=Journal()
    other.append(at=AT,kind="test",reason="explain",policy_hash="a",inputs={"source":"b"})
    assert other.head==h


def test_broken_source_records_only_a_skip_and_stop_breached_open_never_enters():
    g=gateway()
    assert g.reserve(plan(source={"page":None,"quote":""}),D(100),None,AT) is None
    assert g.journal.events[-1]["inputs"]["source_complete"] is False
    assert not g.state.reservations
    assert g.reserve(plan(),D(95),None,AT) is None
    assert not g.state.reservations
