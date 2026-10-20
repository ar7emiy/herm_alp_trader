from __future__ import annotations

from types import SimpleNamespace

from crown.letter.verify import FactReading, TradeFact, match_fact, scalar_disagreements, verify_letter
from crown.letter.schema import Source
from tests.test_letter_extraction import _position


def fact(**overrides):
    data = dict(label="XYZ long", symbols=["XYZ"], kind="long_stock_etf", state="planned",
                entry_price=100, entry_source=Source(page=1, quote="Entry 100"), stop_price=95,
                stop_kind="touch", stop_sessions=None, stop_source=Source(page=1, quote="Touch stop 95"),
                risk_units=.5, risk_source=Source(page=1, quote="Risk 0.5 RU"), no_add=False,
                targets=[], invalidations=[], source=Source(page=1, quote="XYZ long"))
    return TradeFact(**(data | overrides))


def test_independent_scalar_disagreement_blocks_silent_fixes():
    p = _position("p", "XYZ", "long", "bullish")
    assert scalar_disagreements(p, fact()) == []
    assert len(scalar_disagreements(p, fact(risk_units=1, stop_price=90))) == 2
    assert p.stop.level == 95 and p.sizing_multiplier == .5


def test_ambiguous_reopened_or_duplicate_symbol_does_not_guess_a_match():
    p = _position("p", "XYZ", "long", "bullish")
    assert match_fact(p, [fact(), fact(label="XYZ second setup")]) is None
    assert match_fact(p, [fact(state="closed")]) is None


def test_independent_reader_never_receives_the_first_model_output(cfg, tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"synthetic test PDF")
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        assert kwargs["store"] is False
        assert kwargs["text_format"] is FactReading
        assert "crown_positions" not in str(kwargs["input"])
        return SimpleNamespace(status="completed", output_parsed=FactReading(
            letter_date="2026-06-01", trades=[fact()], pages_read=[1], uncertainties=[]),
            model="test", usage=None)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    verify_letter(pdf, cfg, client)
    verify_letter(pdf, cfg, client)
    assert len(calls) == 1
