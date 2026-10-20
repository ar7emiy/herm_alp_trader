"""Independent vision corroboration of printed execution facts, not approval.

The second reader does not see the first extraction. Agreement is useful evidence,
not proof that two calls to the same model cannot share an error. Research replay
retains that limitation and rejects discrepancies on every actionable scalar.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

from crown.config import Config, openai_api_key
from crown.letter.schema import CrownPosition, ExtractionRecord, Source


class TradeFact(BaseModel):
    label: str
    symbols: list[str]
    kind: Literal["long_stock_etf", "option", "pair", "future_fx", "other"]
    state: Literal["held", "planned", "conditional", "watch_only", "closed"]
    entry_price: float | None
    entry_source: Source | None
    stop_price: float | None
    stop_kind: Literal["touch", "close", "ambiguous", "none"]
    stop_sessions: int | None
    stop_source: Source | None
    risk_units: float | None
    risk_source: Source | None
    no_add: bool
    targets: list[float]
    invalidations: list[str]
    source: Source


class FactReading(BaseModel):
    letter_date: str
    trades: list[TradeFact]
    pages_read: list[int]
    uncertainties: list[str]


class VerificationRecord(BaseModel):
    pdf_sha256: str
    model: str
    request_fingerprint: str
    at: str
    usage: dict
    reading: FactReading


PROMPT = """Read this Crown letter independently. Return printed trade execution facts
from EVERY page. Do not use memory of prices or other issues. Do not decide trades.
Include every current instrument setup (held, planned, conditional, watchlist) and
separate contingent additions. Closed trades go in separate closed rows.
Classify explicit long stock/ETF separately from options, pairs and futures/FX.
Copy single underlying entry prices only; option debit/credit is NOT an underlying
entry price. Unknown/range entry is null. A chart's current price is not an entry.
Read risk-unit badges such as RISK 1 RU and sizing 0.25. A percentage capital weight
is NOT risk units. Unknown risk_units is null. Never infer it from conviction.
Copy stated underlying stops with page quotes. stop_kind touch requires explicit
touch/trade/stop-order language. close requires explicit closing-price language.
A bare stop number without observation convention is ambiguous. A max option debit
is NOT a dollar stop on the underlying. Reclaim zones remain ambiguous, not midpoint
stops. Consecutive closes with no session count: stop_sessions null, uncertainty.
Never change a stop from the letter or infer that a proposed risk is an actual exit.
no_add is true for carry-only/no new additions/hold existing upside restrictions.
Use the actual printed instrument labels; don't convert ETF/index or futures prices.
Preserve uncertainties and every page number (including covers/disclaimers).
Source quotes must be short literal fragments. Symbols are tickers/roots, not prose.
Do not invent a position for a house view or a macro discussion without an instrument.
"""


def verification_fingerprint(cfg: Config) -> str:
    return hashlib.sha256(json.dumps({
        "prompt": PROMPT, "schema": FactReading.model_json_schema(),
        "model": cfg.llm.extract_model, "detail": cfg.llm.extract_pdf_detail,
        "effort": cfg.llm.extract_reasoning_effort, "output": 16384,
    }, sort_keys=True).encode()).hexdigest()


def verification_path(pdf: Path, cfg: Config) -> Path:
    return cfg.paths.extracted_letters / "verification" / f"{pdf.stem}.{verification_fingerprint(cfg)[:16]}.json"


def load_verification(pdf: Path, cfg: Config) -> VerificationRecord | None:
    path = verification_path(pdf, cfg)
    if not path.exists():
        return None
    record = VerificationRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if record.pdf_sha256 != hashlib.sha256(pdf.read_bytes()).hexdigest():
        return None
    if record.request_fingerprint != verification_fingerprint(cfg):
        return None
    return record


def verify_letter(pdf: Path, cfg: Config, client=None) -> VerificationRecord:
    cached = load_verification(pdf, cfg)
    if cached:
        return cached
    content = pdf.read_bytes()
    client = client or OpenAI(api_key=openai_api_key())
    kwargs = {"reasoning": {"effort": cfg.llm.extract_reasoning_effort}} if cfg.llm.extract_reasoning_effort else {}
    response = client.responses.parse(
        model=cfg.llm.extract_model, instructions=PROMPT,
        input=[{"role": "user", "content": [
            {"type": "input_file", "filename": pdf.name, "detail": cfg.llm.extract_pdf_detail,
             "file_data": "data:application/pdf;base64," + base64.b64encode(content).decode()},
            {"type": "input_text", "text": "Read each printed trade and its risk/entry/exit fields independently."},
        ]}], text_format=FactReading, max_output_tokens=16384, store=False, **kwargs,
    )
    if response.status != "completed" or response.output_parsed is None:
        raise RuntimeError("Independent fact reading incomplete; no verification accepted")
    record = VerificationRecord(pdf_sha256=hashlib.sha256(content).hexdigest(), model=response.model,
                                request_fingerprint=verification_fingerprint(cfg),
                                at=datetime.now(timezone.utc).isoformat(),
                                usage=response.usage.model_dump() if response.usage else {},
                                reading=response.output_parsed)
    path = verification_path(pdf, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve completed reads when content under a filename changes.
    if path.exists():
        old = path.read_bytes()
        (path.parent / f"{hashlib.sha256(old).hexdigest()}.history.json").write_bytes(old)
    temp = path.with_suffix(".tmp")
    temp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    temp.replace(path)
    return record


def match_fact(position: CrownPosition, facts: list[TradeFact]) -> TradeFact | None:
    """Exact, unique symbol/family match only. Ambiguous candidates are blocked."""
    symbols = {l.underlying.upper() for l in position.structure.legs}
    if not symbols:
        symbols = {position.instrument.primary.upper()}
    expected = "long_stock_etf" if position.structure.type == "long" and position.instrument.asset_class in {"equity", "etf"} else None
    allowed = symbols | {s.upper() for s in position.instrument.alternatives}
    matches = [f for f in facts if f.state != "closed"
               and symbols <= set(s.upper() for s in f.symbols) <= allowed
               and (expected is None or f.kind == expected)]
    return matches[0] if len(matches) == 1 else None


def printed_entry(fact: TradeFact) -> float | None:
    """A narrow literal normalization of a single quoted stock/ETF entry.

    Preserve the model's reading separately. Do not read option premiums or ranges
    as stock prices, or infer a price from a chart/current mark.
    """
    if fact.entry_price is not None:
        return fact.entry_price
    if fact.kind != "long_stock_etf" or fact.entry_source is None:
        return None
    text = fact.entry_source.quote.strip()
    match = re.fullmatch(r"(?i)entry\s*[:\u2014\-]?\s*[~\u2248]?\s*\$?([0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def scalar_disagreements(position: CrownPosition, fact: TradeFact) -> list[str]:
    issues = []
    for field, a, b in (
        ("risk_units", position.sizing_multiplier, fact.risk_units),
        ("entry_price", position.entry.level, printed_entry(fact)),
        ("stop_price", position.stop.level, fact.stop_price),
        ("stop_sessions", position.stop.sessions, fact.stop_sessions),
    ):
        if a != b:
            issues.append(f"{field}: extraction={a!r}, independent_read={b!r}")
    if fact.state == "watch_only" and position.setup_state == "active":
        issues.append("setup state disagrees: independent reader says watch_only")
    return issues


def corroboration(record: ExtractionRecord, verification: VerificationRecord, page_count: int) -> dict:
    if verification.pdf_sha256 != record.pdf_sha256:
        raise ValueError("Independent reading belongs to another PDF")
    x = verification.reading
    complete = sorted(x.pages_read) == list(range(1, page_count + 1))
    normalized_date = x.letter_date
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            normalized_date = datetime.strptime(x.letter_date, fmt).date().isoformat()
            break
        except ValueError:
            continue
    rows = []
    for p in record.extraction.crown_positions:
        if p.instrument.asset_class not in {"equity", "etf"} or p.structure.type != "long":
            continue
        f = match_fact(p, x.trades)
        errors = scalar_disagreements(p, f) if f else ["No unique independent fact match"]
        if not complete or normalized_date != record.extraction.letter_date:
            errors.append("Independent page/date coverage mismatch")
        if f:
            for source in (f.source, f.entry_source, f.stop_source, f.risk_source):
                if source and (source.page is None or not 1 <= source.page <= page_count or not source.quote.strip()):
                    errors.append("Invalid independent source")
        rows.append({"position_key": p.position_key, "agrees": not errors, "issues": errors,
                     "fact": f.model_dump() if f else None})
    return {"letter_date": record.extraction.letter_date, "pdf_sha256": record.pdf_sha256,
            "scope": "plain long stocks/ETFs only; agreement is not source certification or approval",
            "pages_complete": complete, "positions": rows,
            "date_agrees": normalized_date == record.extraction.letter_date,
            "uncertainties": x.uncertainties}
