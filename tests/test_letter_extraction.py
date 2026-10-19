"""Phase 0 extraction plumbing: schema is OpenAI-strict-compatible, the
extractor caches by extractor version and records code-owned metadata, and
the inventory aggregates cached extractions without any API calls."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.lib._pydantic import to_strict_json_schema

from crown.letter import extract as extract_mod
from crown.letter.inventory import build_report, needs_substitution
from crown.letter.schema import (
    Conviction, CrownPosition, Entry, Instrument, LetterExtraction, LifecycleUpdate,
    Source, Stop, Structure, Thesis,
)

SRC = Source(page=1, quote="q")


def _position(key: str, primary: str, stype: str, view: str, asset_class: str = "etf",
              status: str = "entered") -> CrownPosition:
    return CrownPosition(
        position_key=key, thesis_key="t1", status=status,
        instrument=Instrument(primary=primary, alternatives=[], asset_class=asset_class),
        structure=Structure(type=stype, legs=[], net_price=None, net_price_kind="unknown",
                            max_loss_per_unit=None, max_gain_per_unit=None,
                            breakeven_low=None, breakeven_high=None),
        view=view,
        entry=Entry(type="entered", level=100.0, band_low=None, band_high=None,
                    condition=None, triggered=True, window_close=None),
        stop=Stop(type="price", level=95.0, dollar=None, rule=None, sessions=None),
        targets=[], sizing_multiplier=0.5, event_dependency=None, management_notes=None,
        hedges_position_keys=[], source=SRC,
    )


def _letter(letter_date: str, positions: list[CrownPosition],
            updates: list[LifecycleUpdate] | None = None) -> LetterExtraction:
    thesis = Thesis(thesis_key="t1", title="t", claim="c", drivers=[], evidence=[],
                    horizon="weeks", invalidation=[],
                    conviction=Conviction(dots=4, raw="●●●●○", flag="ok"),
                    house_view_relation="unclear", source=SRC)
    return LetterExtraction(letter_date=letter_date, is_mid_cycle=False, section_names_seen=[],
                            theses=[thesis], crown_positions=positions,
                            lifecycle_updates=updates or [], levels=[], events=[],
                            house_view=[], review_flags=[])


def test_schema_is_openai_strict_compatible():
    schema = to_strict_json_schema(LetterExtraction)
    assert schema["additionalProperties"] is False


def test_needs_substitution_flags_margin_structures_and_non_alpaca_assets():
    assert needs_substitution(_position("a", "SPY", "long", "bullish")) == []
    assert needs_substitution(_position("b", "META", "bear_call", "bearish"))
    assert needs_substitution(_position("c", "HG", "long", "bullish", asset_class="future"))


class _FakeResponses:
    def __init__(self, parsed: LetterExtraction):
        self.parsed = parsed
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        assert kwargs["text_format"] is LetterExtraction
        assert kwargs["input"][0]["content"][0]["type"] == "input_file"
        return SimpleNamespace(
            status="completed", incomplete_details=None, output=[], model="fake-model",
            output_parsed=self.parsed,
            usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 10, "output_tokens": 5}),
        )


@pytest.fixture
def letters_cfg(cfg, tmp_path: Path):
    letters = tmp_path / "letters"
    letters.mkdir()
    return dataclasses.replace(cfg, backtest=dataclasses.replace(cfg.backtest, letters_dir=str(letters))), letters


def test_extractor_caches_per_version_and_records_metadata(letters_cfg):
    cfg, letters = letters_cfg
    pdf = letters / "6_22_2026_crown_letter.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    fake = _FakeResponses(_letter("2026-06-22", [_position("a", "SPY", "long", "bullish")]))
    client = SimpleNamespace(responses=fake)

    rec = extract_mod.extract_letter(pdf, cfg=cfg, client=client)
    assert rec.extractor_version == cfg.llm.extractor_version
    assert rec.model == "fake-model" and len(rec.pdf_sha256) == 64
    assert extract_mod.cache_path(cfg, pdf).exists()

    extract_mod.extract_letter(pdf, cfg=cfg, client=client)
    assert fake.calls == 1, "second call must hit the cache"

    bumped = dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, extractor_version="v-next"))
    extract_mod.extract_letter(pdf, cfg=bumped, client=client)
    assert fake.calls == 2, "a new extractor version must re-extract"


def test_extractor_raises_on_incomplete_response(letters_cfg):
    cfg, letters = letters_cfg
    pdf = letters / "x.pdf"
    pdf.write_bytes(b"%PDF")

    class Incomplete:
        def parse(self, **_):
            return SimpleNamespace(status="incomplete", incomplete_details="max_output_tokens",
                                   output=[], output_parsed=None, usage=None, model="m")

    with pytest.raises(RuntimeError, match="incomplete"):
        extract_mod.extract_letter(pdf, cfg=cfg, client=SimpleNamespace(responses=Incomplete()))


def test_inventory_counts_gaps_and_silent_drops(letters_cfg):
    cfg, letters = letters_cfg
    series = {
        "a.pdf": _letter("2026-06-01", [_position("p1", "SPY", "long", "bullish"),
                                         _position("p2", "META", "bear_call", "bearish")]),
        # META dropped without any update; SPY still listed
        "b.pdf": _letter("2026-06-08", [_position("p1", "SPY", "long", "bullish")]),
        # two-week gap; SPY closed via an update, so it is NOT a silent drop
        "c.pdf": _letter("2026-06-22", [], updates=[LifecycleUpdate(
            position_ref="SPY long", kind="closed", detail="took profit", exit_level=None,
            reported_pnl=None, source=SRC)]),
    }
    for name, letter in series.items():
        (letters / name).write_bytes(b"%PDF")
        fake = SimpleNamespace(responses=_FakeResponses(letter))
        extract_mod.extract_letter(letters / name, cfg=cfg, client=fake)

    report = build_report(cfg)
    assert "2026-06-08 → 2026-06-22 (14 days)" in report
    assert "Possibly dropped silently (heuristic): 1" in report
    assert "META bear_call (bearish): in 2026-06-01, absent from 2026-06-08" in report
    assert "Need substitution or skip: 1/2 (50%)" in report
