"""Offline Phase 0 checks. A schema-valid extraction is not approved trade input.

These checks cannot prove that a vision model read a page correctly. A separate
page review records omissions and misreadings against the exact extraction hash.
No function in this module grants trading or planning approval.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from crown.letter.schema import ExtractionRecord


class AuditIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["error", "review"]
    code: str
    field: str
    detail: str


class ReviewCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    passed: bool
    critical: bool = True
    detail: str
    source_pages: list[int]


class PageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extraction_sha256: str
    reviewer: str = Field(min_length=1)
    pages_reviewed: list[int]
    issues: list[AuditIssue]
    checks: list[ReviewCheck] = Field(default_factory=list)


class ExtractionAudit(BaseModel):
    pdf_name: str
    pdf_sha256: str
    extraction_sha256: str
    page_count: int
    audited_at: str
    # A later phase must implement approval; absence of an issue is not approval.
    planning_status: Literal["blocked", "manual_review_required"]
    page_review: PageReview | None
    transcription_status: Literal["not_reviewed", "needs_correction", "critical_checks_passed"]
    issues: list[AuditIssue]


def record_hash(record: ExtractionRecord) -> str:
    return hashlib.sha256(record.model_dump_json().encode("utf-8")).hexdigest()


def latest_page_review(record: ExtractionRecord, reports_dir: Path) -> PageReview | None:
    digest = record_hash(record)
    candidates = []
    for path in (reports_dir / "audits").glob("*.json"):
        try:
            report = ExtractionAudit.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if report.extraction_sha256 == digest and report.page_review is not None:
            candidates.append(report)
    return max(candidates, key=lambda r: r.audited_at).page_review if candidates else None


def audit_record(record: ExtractionRecord, page_count: int,
                 review: PageReview | None = None) -> ExtractionAudit:
    if page_count < 1:
        raise ValueError("PDF must contain at least one page")
    digest = record_hash(record)
    if review is not None and review.extraction_sha256 != digest:
        raise ValueError("Page review belongs to a different extraction")
    issues: list[AuditIssue] = []

    def add(code: str, field: str, detail: str, severity: str = "error") -> None:
        issues.append(AuditIssue(severity=severity, code=code, field=field, detail=detail))

    def check_date(value: str | None, field: str) -> None:
        if value is None:
            return
        try:
            date.fromisoformat(value)
        except ValueError:
            add("invalid_date", field, "Expected an ISO calendar date")

    def walk(value, path: str = "extraction") -> None:
        if isinstance(value, dict):
            if "source" in value:
                src = value["source"]
                page = src["page"]
                if page is None or not 1 <= page <= page_count:
                    add("source_page", f"{path}.source.page", "Source page is missing or outside the PDF")
                if not src["quote"].strip():
                    add("source_quote", f"{path}.source.quote", "Source quote is empty")
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, float) and not math.isfinite(value):
            add("nonfinite_number", path, "Non-finite numbers cannot be used in a plan")

    x = record.extraction
    walk(x.model_dump())
    covered = [p.page for p in x.page_coverage]
    if len(covered) != len(set(covered)) or set(covered) != set(range(1, page_count + 1)):
        add("page_coverage", "page_coverage", "Each PDF page must be accounted for exactly once")
    check_date(x.letter_date, "letter_date")
    thesis_keys = [t.thesis_key for t in x.theses]
    position_keys = [p.position_key for p in x.crown_positions]
    for coverage in x.page_coverage:
        if any(key not in position_keys for key in coverage.position_keys):
            add("coverage_reference", "page_coverage", "Page coverage refers to an absent position")
    for field, keys in (("theses", thesis_keys), ("crown_positions", position_keys)):
        for key, count in Counter(keys).items():
            if not key.strip() or count > 1:
                add("duplicate_or_empty_key", field, f"Invalid local key: {key!r}")

    for i, thesis in enumerate(x.theses):
        path = f"theses[{i}]"
        conviction = thesis.conviction
        if conviction.raw is not None:
            filled = conviction.raw.count("\u25cf")
            total = filled + conviction.raw.count("\u25cb")
            if total:
                expected = "ok" if total == 5 else "glyph_count_mismatch"
                if conviction.dots != filled or conviction.flag != expected:
                    add("conviction_inconsistent", f"{path}.conviction",
                        "Count/flag disagrees with the extracted glyphs; check the actual page")
                elif total != 5:
                    add("conviction_scale", f"{path}.conviction",
                        "Printed conviction scale needs page review", "review")
        for j, condition in enumerate(thesis.invalidation):
            low, high = condition.band_low, condition.band_high
            if (low is None) != (high is None) or (low is not None and low > high):
                add("invalidation_band", f"{path}.invalidation[{j}]", "Invalidation range is incomplete or reversed")
            if condition.kind == "price_level" and (
                condition.instrument is None or condition.op is None or condition.level is None
            ):
                add("invalidation_ambiguous", f"{path}.invalidation[{j}]",
                    "Price invalidation lacks an instrument, comparison, or level", "review")

    for i, p in enumerate(x.crown_positions):
        path = f"crown_positions[{i}]"
        if p.thesis_key not in thesis_keys:
            add("unknown_thesis", f"{path}.thesis_key", "Position refers to an absent thesis")
        for key in p.hedges_position_keys:
            if key not in position_keys or key == p.position_key:
                add("unknown_hedge", f"{path}.hedges_position_keys", "Invalid hedge relationship")
        if p.status == "entered" and not p.entry.triggered:
            add("entry_status", f"{path}.entry", "Entered position is marked untriggered")
        if p.setup_state in ("watch_only", "superseded", "unclear"):
            add("setup_not_actionable", f"{path}.setup_state",
                "This setup is not an active entry instruction", "review")
        check_date(p.entry.window_close, f"{path}.entry.window_close")
        low, high = p.entry.band_low, p.entry.band_high
        if (low is None) != (high is None) or (low is not None and low > high):
            add("entry_band", f"{path}.entry", "Entry band is incomplete or reversed")
        if p.sizing_multiplier is not None and p.sizing_multiplier <= 0:
            add("sizing_multiplier", f"{path}.sizing_multiplier", "Sizing multiplier must be positive")
        for field in ("net_price", "max_loss_per_unit", "max_gain_per_unit"):
            value = getattr(p.structure, field)
            if value is not None and value < 0:
                add("negative_amount", f"{path}.structure.{field}", "Amount must be unsigned; use price kind for debit/credit")
        option_legs = [leg for leg in p.structure.legs if leg.right in ("call", "put")]
        if option_legs or p.instrument.asset_class in ("option_on_equity", "option_on_future"):
            add("option_units_unverified", f"{path}.structure",
                "Raw quoted premium/risk is not contract dollars. Resolve contract multiplier, "
                "deliverable, quote units and leg ratios before sizing; do not assume 100 for adjusted contracts.", "review")
        for j, leg in enumerate(p.structure.legs):
            field = f"{path}.structure.legs[{j}]"
            check_date(leg.expiry, f"{field}.expiry")
            if leg.ratio is not None and leg.ratio <= 0:
                add("leg_ratio", f"{field}.ratio", "Leg ratio must be positive")
            if leg.ratio is not None and leg.ratio_kind in ("notional_weight", "not_stated"):
                add("ratio_units_unverified", field, "Weight/unknown units cannot be used as executable leg quantities", "review")
            if leg.right in ("call", "put"):
                if leg.strike is not None and leg.strike <= 0:
                    add("option_leg", field, "Option strike must be positive")
                if leg.strike is None or leg.expiry is None:
                    add("option_leg_incomplete", field,
                        "Missing strike/expiry blocks implementation; preserve unstated values as null", "review")

    levels = defaultdict(set)
    for level in x.levels:
        levels[(level.instrument.upper(), level.kind)].add(level.level)
    for (instrument, kind), values in levels.items():
        if len(values) > 1:
            add("multiple_reference_levels", "levels",
                f"{instrument} has multiple {kind} levels; resolve their source/time context", "review")
    for flag in x.review_flags:
        add("extractor_flag", flag.field, flag.reason, "review")
    if record.received_at is None:
        add("receipt_unknown", "received_at",
            "Receipt time is unknown; do not assume publication date or PDF export time was availability", "review")
    if review is not None:
        pages = review.pages_reviewed
        if len(pages) != len(set(pages)) or any(not 1 <= p <= page_count for p in pages):
            add("review_pages", "page_review.pages_reviewed", "Review page list is invalid")
        if set(pages) != set(range(1, page_count + 1)):
            add("review_incomplete", "page_review.pages_reviewed", "Some PDF pages have not been reviewed", "review")
        issues.extend(review.issues)
        for check in review.checks:
            if not check.passed:
                add("page_check_failed", check.name, check.detail)
            if not check.source_pages or any(not 1 <= p <= page_count for p in check.source_pages):
                add("page_check_source", check.name, "Page check requires valid source pages")
    transcription_status = "not_reviewed"
    if review is not None:
        transcription_status = "needs_correction"
        if (
            set(review.pages_reviewed) == set(range(1, page_count + 1))
            and review.checks and all(c.passed for c in review.checks)
            and any(c.critical for c in review.checks)
            and not any(i.severity == "error" for i in issues)
        ):
            transcription_status = "critical_checks_passed"
    return ExtractionAudit(
        pdf_name=record.pdf_name, pdf_sha256=record.pdf_sha256,
        extraction_sha256=digest, page_count=page_count,
        audited_at=datetime.now(timezone.utc).isoformat(),
        planning_status="blocked" if issues else "manual_review_required",
        page_review=review, issues=issues,
        transcription_status=transcription_status,
    )


def write_audit(report: ExtractionAudit, reports_dir: Path) -> Path:
    """Content-address the report so later reviews cannot erase earlier findings."""
    content = report.model_dump_json(indent=2)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    out = reports_dir / "audits" / f"{Path(report.pdf_name).stem}.{digest}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        stream.write(content)
    return out
