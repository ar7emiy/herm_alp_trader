from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from crown.letter.audit import PageReview, ReviewCheck, audit_record, record_hash
from crown.letter.correct import CorrectionBatch, CorrectionEdit, apply_corrections
from crown.letter.extract import cache_path, extract_letter, load_cached
from crown.letter.preview import build_preview, preliminary_screen, risk_budget, usage_summary
from crown.letter.schema import Source
from tests.test_letter_extraction import _FakeResponses, _letter, _position


def _record(cfg, tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    p = _position("p1", "XYZ", "long", "bullish")
    return pdf, extract_letter(pdf, cfg=cfg,
                              client=SimpleNamespace(responses=_FakeResponses(_letter("2026-06-22", [p]))))


def test_correction_preserves_provider_output_and_invalidates_old_review(cfg, tmp_path):
    pdf, original = _record(cfg, tmp_path)
    batch = CorrectionBatch(extraction_sha256=record_hash(original), reviewer="test-page-review",
                            changes=[CorrectionEdit(
                                path="/crown_positions/0/entry/level", before=100, after=101,
                                source=Source(page=1, quote="Synthetic entry 101"), reason="Misread digit")])
    updated = apply_corrections(pdf, batch, cfg)
    assert original.extraction.crown_positions[0].entry.level == 100
    assert updated.extraction.crown_positions[0].entry.level == 101
    assert updated.usage == original.usage and updated.extracted_at == original.extracted_at
    assert len(updated.corrections) == 1
    assert load_cached(cfg, pdf).corrections == updated.corrections
    assert record_hash(updated) != record_hash(original)
    with pytest.raises(ValueError, match="active extraction"):
        apply_corrections(pdf, batch, cfg)


def test_failed_correction_is_transactional_and_cannot_change_envelope(cfg, tmp_path):
    pdf, record = _record(cfg, tmp_path)
    before = cache_path(cfg, pdf).read_bytes()
    batch = CorrectionBatch(extraction_sha256=record_hash(record), reviewer="test", changes=[
        CorrectionEdit(path="/crown_positions/0/entry/level", before=100, after=101,
                       source=Source(page=1, quote="synthetic"), reason="correct"),
        CorrectionEdit(path="/model", before=record.model, after="different",
                       source=Source(page=1, quote="synthetic"), reason="invalid envelope mutation"),
    ])
    with pytest.raises(ValueError):
        apply_corrections(pdf, batch, cfg)
    assert cache_path(cfg, pdf).read_bytes() == before


def test_report_escapes_letter_content_and_does_not_infer_a_trade_or_size(cfg, tmp_path, monkeypatch):
    pdf, record = _record(cfg, tmp_path)
    p = record.extraction.crown_positions[0]
    p.instrument.primary = '<script>alert("private")</script>'
    p.setup_state = "superseded"
    monkeypatch.setattr("crown.letter.preview.load_all", lambda _: ([record], []))
    report = build_preview(cfg)
    assert '<script>alert("private")</script>' not in report
    assert "&lt;script&gt;" in report
    assert "Watch / review" in report and "Orders disabled" in report
    assert "backtest returns" in report
    assert risk_budget(p, cfg) == Decimal("37.5")
    assert preliminary_screen(p)[0] == "Watch / review"


def test_option_spread_is_not_a_cash_compatible_plain_wrapper():
    p = _position("p1", "XYZ", "debit_call_spread", "bullish", "option_on_equity")
    assert preliminary_screen(p)[0] == "Capability review"


def test_spend_counts_corrected_and_archived_attempt_only_once(cfg, tmp_path):
    pdf, original = _record(cfg, tmp_path)
    record = original.model_dump()
    record["model"] = "gpt-5-2025-08-07"
    record["usage"] = {"input_tokens": 1_000_000, "output_tokens": 1000,
                       "input_tokens_details": {"cached_tokens": 100_000}}
    active = cache_path(cfg, pdf)
    active.write_text(json.dumps(record), encoding="utf-8")
    archive = active.parent / "history" / "provider-attempt.json"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(json.dumps(record), encoding="utf-8")
    # Remove the test helper's unpriced fake attempt from this dedicated estimate.
    for old in (active.parent / "history").rglob("*.json"):
        if old != archive:
            old.unlink()
    record["corrections"] = [{"reviewer": "test"}]
    active.write_text(json.dumps(record), encoding="utf-8")
    summary = usage_summary(cfg)
    assert summary["attempts"] == 1
    assert Decimal(summary["estimated_usd"]) == Decimal("1.1475")


def test_page_coverage_cannot_be_faked_by_duplicate_pages(cfg, tmp_path):
    _, record = _record(cfg, tmp_path)
    record.extraction.page_coverage *= 2
    report = audit_record(record, page_count=2)
    assert any(i.code == "page_coverage" for i in report.issues)


def test_missing_source_expiry_is_a_review_need_not_a_license_to_invent(cfg, tmp_path):
    from crown.letter.schema import Leg

    _, record = _record(cfg, tmp_path)
    record.extraction.crown_positions[0].structure.legs = [Leg(
        action="buy", right="call", underlying="XYZ", strike=50,
        expiry=None, ratio=None, ratio_kind="not_stated")]
    report = audit_record(record, 1)
    assert any(i.code == "option_leg_incomplete" and i.severity == "review" for i in report.issues)
    assert record.extraction.crown_positions[0].structure.legs[0].expiry is None
    assert report.planning_status == "blocked"


def test_failed_critical_page_check_cannot_be_described_as_passed(cfg, tmp_path):
    _, record = _record(cfg, tmp_path)
    review = PageReview(extraction_sha256=record_hash(record), reviewer="test", pages_reviewed=[1],
                        issues=[], checks=[ReviewCheck(name="entry", passed=False, detail="Wrong digit", source_pages=[1])])
    report = audit_record(record, 1, review)
    assert report.transcription_status == "needs_correction"
    assert report.planning_status == "blocked"
