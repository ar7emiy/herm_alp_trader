from datetime import date

from crown.data.calendar import market_open_close
from crown.letter.schema import ExtractionRecord, InvalidationCondition, Source
from crown.letter.verify import FactReading, VerificationRecord
from crown.research.compiler import availability, compile_records
from crown.research.models import ResearchPolicy
from tests.test_independent_facts import fact
from tests.test_letter_extraction import _letter,_position


def source(day="2026-06-01",positions=None,facts=None):
    rec=ExtractionRecord(pdf_name="synthetic.pdf",pdf_sha256="a"*64,received_at=None,extractor_version="test",
        model="synthetic",extracted_at="2026-10-19T12:00:00Z",usage={},
        extraction=_letter(day,positions or [_position("p","XYZ","long","bullish")]))
    v=VerificationRecord(pdf_sha256="a"*64,model="synthetic",request_fingerprint="test",at="2026-10-19T12:00:00Z",usage={},
        reading=FactReading(letter_date=day,trades=facts or [fact()],pages_read=[1],uncertainties=[]))
    return rec,v,1


def test_unknown_receipt_is_explicit_and_later_than_printed_date():
    rec,_,_=source()
    at,assumption=availability(rec)
    assert at>market_open_close(date(2026,6,2))[0]
    assert "unknown" in assumption


def test_critical_disagreement_or_unavailable_thesis_evidence_blocks():
    r,v,n=source(facts=[fact(risk_units=1)])
    p=compile_records([(r,v,n)],ResearchPolicy())[0].plans[0]
    assert not p.allowed and any("disagree" in x for x in p.reasons)
    r,v,n=source()
    r.extraction.theses[0].invalidation=[InvalidationCondition(kind="narrative",instrument=None,op=None,level=None,
        band_low=None,band_high=None,sessions=None,event_ref=None,text="Curve reverses",source=Source(page=1,quote="curve"))]
    p=compile_records([(r,v,n)],ResearchPolicy.protective_research())[0].plans[0]
    assert not p.allowed and any("reference" in x for x in p.reasons)


def test_option_closure_never_closes_stock_episode_and_reopen_is_new_episode():
    first=source()
    overlay=source("2026-06-08",facts=[fact(),fact(kind="option",state="closed")])
    closed=source("2026-06-15",positions=[_position("p","XYZ","long","bullish",status="closed")],facts=[fact(state="closed")])
    reopened=source("2026-06-22")
    bs=compile_records([first,overlay,closed,reopened],ResearchPolicy())
    pid=bs[0].plans[0].position_id
    assert bs[1].plans[0].position_id==pid and not bs[1].closures
    assert bs[2].closures==(pid,)
    assert bs[3].plans[0].position_id!=pid


def test_short_volatility_is_skip_not_a_directional_proxy():
    s=source(positions=[_position("p","XYZ","iron_condor","neutral_range")])
    p=compile_records([s],ResearchPolicy())[0].plans[0]
    assert not p.allowed and any("Unsupported payoff" in x for x in p.reasons)


def test_management_restriction_overrules_two_readers_missing_no_add():
    r,v,n=source()
    r.extraction.crown_positions[0].management_notes="Managing positions rather than adding more exposure"
    p=compile_records([(r,v,n)],ResearchPolicy.protective_research())[0].plans[0]
    assert not p.allowed and any("restriction" in reason for reason in p.reasons)


def test_missing_companion_hedge_blocks_otherwise_valid_core():
    r,v,n=source()
    r.extraction.crown_positions[0].hedges_position_keys=["unsupported-put"]
    p=compile_records([(r,v,n)],ResearchPolicy.protective_research())[0].plans[0]
    assert not p.allowed and any("hedge" in reason for reason in p.reasons)


def test_late_old_closure_cannot_change_an_episode_before_receipt():
    first = source()
    late = source("2026-06-08", positions=[_position("p", "XYZ", "long", "bullish", status="closed")],
                  facts=[fact(state="closed")])
    late[0].received_at = "2026-06-30T13:00:00+00:00"
    newer = source("2026-06-15")
    bs = compile_records([first, late, newer], ResearchPolicy())
    assert bs[0].plans[0].position_id == bs[1].plans[0].position_id
    assert bs[2].plans == () and bs[2].closures == ()
