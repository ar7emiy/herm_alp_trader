from dataclasses import replace
from datetime import date, timedelta

import pytest

from crown.data.calendar import market_open_close, trading_days
from crown.research.compiler import LetterBatch
from crown.research.data import prior_atr
from crown.research.models import Bar, D, ResearchPolicy
from crown.research.runner import run
from tests.test_research_gateway import plan


START=date(2026,6,1)


def bar(day,op=100,hi=102,lo=98,close=101,symbol="ETF"):
    return Bar(symbol,day,*market_open_close(day),*(D(v) for v in (op,hi,lo,close,1000)))


def replay(series,plans=None,policy=None,batches=None):
    p=plan(available_at=market_open_close(START)[0])
    batches=batches if batches is not None else [LetterBatch(p.available_at,"2026-06-01",tuple(plans or [p]),(),"Synthetic exact receipt")]
    return run(batches,{"ETF":{b.day:b for b in series}},policy or ResearchPolicy(),START,max(b.day for b in series),"manifest")


def exits(result):return [e for e in result["events"] if e["kind"]=="exit_fill"]


def test_stop_wins_intraday_target_and_gap_has_no_guaranteed_stop_fill():
    p=plan(available_at=market_open_close(START)[0],targets=(D(105),))
    r=replay([bar(START,100,110,90,102)],plans=[p])
    assert len(exits(r))==1 and D(exits(r)[0]["price"])==D('94.9525')
    r=replay([bar(START),bar(START+timedelta(days=1),90,92,89,91)])
    assert D(exits(r)[0]["price"])==D('89.955')
    assert D(r["net_pnl"])<-75  # Gaps exceed intended risk budget.


def test_close_stop_waits_for_confirmation_and_next_open():
    p=plan(available_at=market_open_close(START)[0],stop_kind="close",stop_sessions=2)
    r=replay([bar(START,100,101,90,94),bar(date(2026,6,2),94,96,92,94),bar(date(2026,6,3),93,97,91,96)],plans=[p])
    assert len(exits(r))==1
    assert exits(r)[0]["at"]==market_open_close(date(2026,6,3))[0].isoformat()
    assert D(exits(r)[0]["price"])==D('92.9535')


def test_published_stop_update_cannot_loosen_and_target_trim_is_not_reentry():
    at=market_open_close(START)[0]
    p=plan(available_at=at,targets=(D(105),D(110)))
    next_p=replace(p,available_at=market_open_close(date(2026,6,2))[0],stop=D(90),record_sha256="next")
    bs=[LetterBatch(at,"2026-06-01",(p,),(),"exact"),LetterBatch(next_p.available_at,"2026-06-02",(next_p,),(),"exact")]
    r=replay([bar(START,100,106,99,105),bar(date(2026,6,2),105,106,99,104),bar(date(2026,6,3))],batches=bs)
    assert r["counts"]["entry_fill"]==1
    assert r["counts"]["stop_rejected"]==1
    assert len(exits(r))==2


def test_evidence_expiry_and_missing_data_do_not_keep_thesis_alive():
    p=plan(available_at=market_open_close(START)[0],expires_on=START)
    r=replay([bar(START),bar(date(2026,6,3))],plans=[p])
    assert r["counts"]["data_missing"]==1
    assert not r["open_positions"]
    assert "expired" in exits(r)[0]["reason"]


def test_future_letter_and_future_close_cannot_change_earlier_entries():
    at=market_open_close(START)[0]
    p=plan(available_at=at)
    base=LetterBatch(at,"2026-06-01",(p,),(),"exact")
    future=LetterBatch(market_open_close(date(2026,6,8))[0],"2026-06-08",(),("episode",),"exact")
    a=replay([bar(START)],batches=[base])
    b=replay([bar(START)],batches=[base,future])
    assert a["events"]==b["events"] and a["net_pnl"]==b["net_pnl"]
    assert b["future_letters_not_used"]==["2026-06-08"]
    # Changing today's high/low cannot influence the pre-close entry quantity.
    c=replay([bar(START,100,150,97,100)],batches=[base])
    assert next(e for e in a["events"] if e["kind"]=="entry_intent")["quantity"]==next(e for e in c["events"] if e["kind"]=="entry_intent")["quantity"]


def test_atr_excludes_today_and_rejects_missing_prior_session():
    days=trading_days(date(2026,4,1),START)
    series={d:bar(d) for d in days}
    before=prior_atr(series,START)
    series[START]=bar(START,100,1000,1,100)
    assert prior_atr(series,START)==before==D(4)
    del series[days[-3]]
    assert prior_atr(series,START) is None


def test_new_letter_omission_is_a_data_uncertainty_exit_not_claimed_crown_sale():
    at=market_open_close(START)[0]
    p=plan(available_at=at)
    bs=[LetterBatch(at,"2026-06-01",(p,),(),"exact"),
        LetterBatch(market_open_close(date(2026,6,2))[0],"2026-06-02",(),(),"exact")]
    r=replay([bar(START),bar(date(2026,6,2))],batches=bs)
    assert "Missing from new letter" in exits(r)[0]["reason"]


def test_large_raw_price_discontinuity_aborts_instead_of_reporting_split_loss():
    with pytest.raises(RuntimeError,match="corporate-action"):
        replay([bar(START),bar(date(2026,6,2),50,52,49,51)])


def test_late_older_letter_never_overwrites_newer_evidence():
    at=market_open_close(START)[0]
    p=plan(available_at=at)
    old=replace(p,available_at=market_open_close(date(2026,6,2))[0],stop=D(80))
    bs=[LetterBatch(at,"2026-06-01",(p,),(),"exact"),LetterBatch(old.available_at,"2026-05-25",(old,),(),"late")]
    r=replay([bar(START),bar(date(2026,6,2))],batches=bs)
    assert r["counts"]["stale_letter"]==1 and "stop_change" not in r["counts"]


def test_opening_gap_halt_blocks_a_new_symbol_but_keeps_exits_working():
    at=market_open_close(START)[0]
    p=plan(available_at=at,stop=D(50),stop_kind="close",stop_sessions=2)
    # Small loss cap makes an overnight marked loss exceed the policy before
    # a newly received second symbol can be bought.
    policy=ResearchPolicy(daily_loss_limit=D(1))
    next_at=market_open_close(date(2026,6,2))[0]
    other=plan(position_id="second",symbol="OTHER",available_at=next_at)
    bs=[LetterBatch(at,"2026-06-01",(p,),(),"exact"),LetterBatch(next_at,"2026-06-02",(p,other),(),"exact")]
    bars={"ETF":{START:bar(START,100,101,99,100),date(2026,6,2):bar(date(2026,6,2),90,92,89,90)},
          "OTHER":{date(2026,6,2):bar(date(2026,6,2),symbol="OTHER")}}
    r=run(bs,bars,policy,START,date(2026,6,2),"manifest")
    assert r["halted"] and r["counts"]["entry_fill"]==1
    assert any("HALT" in e["reason"] for e in r["events"] if e["kind"]=="skip")
