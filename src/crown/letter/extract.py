"""Letter PDF -> LetterExtraction via one OpenAI vision call (docs/03-letter-model.md).

The Crown letters are image-only PDFs (no text layer), so the PDF is sent as
a file input and the model reads the rendered pages. This is the only LLM
touchpoint for a given letter: results are cached on disk keyed by the PDF
name AND the extractor version, so a letter is extracted once per prompt/
schema version and every downstream stage (and every backtest) replays the
same frozen extraction.

The extractor is a reader, not a judge. It does not size, validate, or
choose instruments — those are later, deterministic stages.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from crown.config import REPO_ROOT, Config, load_config, openai_api_key
from crown.letter.schema import ExtractionRecord, LetterExtraction

GLOSSARY_PATH = REPO_ROOT / "docs" / "12-glossary.md"

SYSTEM_PROMPT = """\
You are the extraction stage of a system that implements the trade ideas in The
Crown Macro Letter, a weekly letter from a macro hedge-fund desk. You read ONE
issue (a PDF of page images) and transcribe it into a structured schema. You do
not judge, size, validate, or choose instruments. Faithful, literal extraction only.

WHAT TO EXTRACT
- theses: what Crown believes and why. One thesis per distinct view (a featured
  trade's thesis, a tactical view, a macro call that has positions attached). Give
  each a short `thesis_key` (lowercase-hyphenated, e.g. "gold-bearish") that is
  unique within this letter.
- crown_positions: every position in the trade table / open trades / new setups /
  featured trades, each linked to its thesis by `thesis_key`, with a unique
  `position_key` within this letter (e.g. "gld-put-spread-385-375").
  Include closed positions only if the letter lists them as positions; closures
  reported in a review section go in lifecycle_updates.
- lifecycle_updates: everything the letter says happened to earlier positions —
  closed, stop moved, target moved, resized, rolled, still open, invalidated — from
  the Previous Week Trade Review / Closed Trades / Open Trades sections or anywhere
  else. `position_ref` is the position as the letter names it.
- levels: every key level (zero gamma, put wall, call wall, support/resistance,
  battleground), on the instrument it is quoted on.
- events: the calendar (What's New This Week) with the trades each event affects.
- house_view: the Portfolio House View rows.
- section_names_seen: the section headings exactly as printed, in order.

RULES
1. NEVER FABRICATE. A value not printed in the letter is null. This applies
   especially to sizing_multiplier, conviction, strikes, expiries, and levels. Do not
   compute values the letter does not state (max loss, breakevens) — copy them only
   if printed.
2. CONVICTION. Count the filled and empty glyphs. If the total glyph count is exactly
   5, flag "ok" and dots = filled count. If it is not 5, flag "glyph_count_mismatch"
   and dots = the filled count; do not guess the intended scale. If no rating is
   printed, flag "not_stated" with dots = null.
3. TRIGGERED VS NOT. "Not triggered", or a conditional whose level has not been hit,
   is entry.triggered = false. Never treat "close to triggering" as triggered.
4. ENTRY WINDOW. If entry timing is tied to an upcoming event or date ("ahead of
   Wednesday's earnings", "entry Mon/Tue only"), set entry.window_close to the last
   valid entry date (YYYY-MM-DD). Otherwise null.
5. INSTRUMENT ALTERNATIVES. Record every instrument Crown names for a position
   ("HG / CPER" -> primary "HG", alternatives ["CPER"]). Do not pick one.
6. INVALIDATION. For each thesis, extract every stated condition that would make
   Crown wrong: a price level (with instrument, close/trade above/below, and session
   count if stated), an event outcome, a regime condition (e.g. losing zero gamma),
   or a narrative condition. If none is stated, leave the list empty.
7. STOPS. A price stop goes in stop.level; a dollar loss in stop.dollar; a rule
   ("close at 50% of max loss if SI sustains above $88 for 2 sessions") in stop.rule
   with stop.type "behavioral" and sessions if stated. If no stop is printed, use
   type "none_stated".
8. NET PRICE. For option structures, net_price is the debit or credit per unit as
   printed and net_price_kind says which. For a pair or spread quoted as a ratio or
   spread level, use net_price_kind "spread_level".
9. SOURCES. Every source has the page number (1-based, as in the PDF) and a short
   verbatim quote (under 30 words) supporting the item.
10. HOUSE VIEW. house_view_relation on a thesis is informational only; set "unclear"
    if the letter does not make it obvious.
11. REVIEW FLAGS. If anything is ambiguous, illegible, or contradicts itself (e.g.
    a number that is hard to read in the image, a table that seems cut off), add a
    review_flag naming the field and the reason, and extract your best literal
    reading. Never silently resolve an ambiguity.
12. COMPLETE PAGE SWEEP. Read every PDF page, including charts and continuation
    pages. Fill page_coverage for EVERY page, even cover/disclaimer pages. Each
    position_keys list refers to positions discussed on that page. Do not limit
    extraction to featured trades: core holdings, same-day expiries, reopened
    baskets, and closed overlays on still-open core holdings are distinct items.
13. QUOTE CONTEXT. Do not omit printed max risk/profit, breakevens or entry bands.
    net_price_context distinguishes actual entry from target, illustration or mark.
    A reference credit in a payoff example is not a guaranteed fill. Preserve all
    target/minimum/skip credits and entry-credit ranges as reported_terms with
    label, literal raw text, and their own page source. Never pick a range midpoint.
    max_loss_unit is quoted_points only if that meaning is clear; otherwise
    not_stated. Never multiply a printed option figure by 100 in extraction.
14. PRICE ZONES. Preserve price invalidation ranges in band_low/band_high, with
    instrument and kind price_level. Leave level, op and sessions null unless
    explicitly stated. "Reclaim" does not by itself mean a daily closing price.
    A stated risk condition is not automatically an instruction to exit at first touch.
15. SETUP STATE. A historical trigger that never hit may be superseded or watch_only
    when Crown describes a different next setup or says no direct trade. Set
    setup_state accordingly; never treat the obsolete trigger as an active order.
    status is a historical position description, not permission for us to enter.
16. RATIO UNITS. Portfolio percentages and hedge notional weights are
    ratio_kind notional_weight, never share/contract counts. If not explicit,
    ratio is null and ratio_kind not_stated. An ETF mentioned in commentary is not
    an instrument alternative unless Crown actually offers it as an expression.
17. CHART IDENTITY. Transcribe chart and table levels on their OWN labeled
    instrument. Never multiply an ETF level into an index level, even if it looks
    like a 10:1 mapping. Keep independently stated observations and source pages;
    flag disagreements between charts/tables. Do not select the one you prefer.
18. GLYPHS. Zoom your attention to the actual conviction row. Transcribe exactly
    the printed glyph count; never add an empty dot to make five. If uncertain,
    use null dots and a review flag rather than completing the scale from habit.
19. REPORTED TERMS. Capture the printed entry, price/premium bands, stops, targets,
    risk, sizing and management terms for EVERY position, including ordinary open
    holdings. Short literal snippets only; do not copy whole paragraphs or calculate
    missing values. Preserve underlying price versus option premium distinctions.

The letter format drifts. Older issues use Featured Trade(s) / Tactical Summary /
Previous Week Trade Review; newer ones use New Trade Setups / Open Trades / Closed
Trades. Map both onto the same schema. A missing or renamed section is not an error.

Reference glossary (Crown's own vocabulary, from their base guide):
"""

USER_PROMPT = (
    "Extract this Crown Macro Letter issue into the schema. Follow the system "
    "instructions exactly: never fabricate values, count conviction glyphs, keep "
    "triggered vs not-triggered literal, and give every item a page + quote source."
)


def _system_prompt() -> str:
    glossary = GLOSSARY_PATH.read_text(encoding="utf-8") if GLOSSARY_PATH.exists() else ""
    return SYSTEM_PROMPT + "\n" + glossary


def cache_path(cfg: Config, pdf_path: Path) -> Path:
    return cfg.paths.extracted_letters / f"{pdf_path.stem}.{cfg.llm.extractor_version}.json"


def _request_fingerprint(cfg: Config, *, instructions: str | None = None) -> str:
    """A prompt/glossary/schema/model change is a different extraction experiment."""
    payload = {
        "instructions": instructions if instructions is not None else _system_prompt(),
        "user_prompt": USER_PROMPT,
        "schema": LetterExtraction.model_json_schema(),
        "model": cfg.llm.extract_model, "reasoning": cfg.llm.extract_reasoning_effort,
        "max_output_tokens": cfg.llm.extract_max_output_tokens,
        "pdf_detail": cfg.llm.extract_pdf_detail,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_cached(cfg: Config, pdf_path: Path) -> ExtractionRecord | None:
    path = cache_path(cfg, pdf_path)
    if not path.exists():
        return None
    record = ExtractionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        record.pdf_sha256 != hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        or record.extractor_version != cfg.llm.extractor_version
        or record.request_fingerprint != _request_fingerprint(cfg)
    ):
        return None
    return record


def _archive(content: str, directory: Path) -> None:
    """Keep every extraction attempt, including superseded active caches."""
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        with (directory / f"{digest}.json").open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError:
        pass  # identical content is already preserved


def extract_letter(
    pdf_path: str | Path,
    cfg: Config | None = None,
    force: bool = False,
    received_at: str | None = None,
    client: OpenAI | None = None,
) -> ExtractionRecord:
    """Extract one letter, using the on-disk cache unless force=True.
    Costs one OpenAI call on a cache miss."""
    cfg = cfg or load_config()
    pdf_path = Path(pdf_path)

    if not force:
        cached = load_cached(cfg, pdf_path)
        if cached is not None:
            return cached

    pdf_bytes = pdf_path.read_bytes()
    client = client or OpenAI(api_key=openai_api_key())
    system_prompt = _system_prompt()
    fingerprint = _request_fingerprint(cfg, instructions=system_prompt)

    kwargs = {}
    if cfg.llm.extract_reasoning_effort:
        kwargs["reasoning"] = {"effort": cfg.llm.extract_reasoning_effort}

    response = client.responses.parse(
        model=cfg.llm.extract_model,
        instructions=system_prompt,
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_file",
                    "detail": cfg.llm.extract_pdf_detail,
                    "filename": pdf_path.name,
                    "file_data": "data:application/pdf;base64,"
                                 + base64.standard_b64encode(pdf_bytes).decode("ascii"),
                },
                {"type": "input_text", "text": USER_PROMPT},
            ],
        }],
        text_format=LetterExtraction,
        max_output_tokens=cfg.llm.extract_max_output_tokens,
        store=False,
        **kwargs,
    )

    if response.status == "incomplete":
        raise RuntimeError(
            f"Extraction of {pdf_path.name} incomplete: {response.incomplete_details} "
            "(raise llm.extract_max_output_tokens in config.yaml?)"
        )
    for item in response.output:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                raise RuntimeError(f"Model refused to extract {pdf_path.name}: {content.refusal}")

    extraction = response.output_parsed
    if extraction is None:
        raise RuntimeError(f"Structured output parsing failed for {pdf_path.name}")

    record = ExtractionRecord(
        pdf_name=pdf_path.name,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        received_at=received_at,
        extractor_version=cfg.llm.extractor_version,
        model=response.model,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        usage=response.usage.model_dump() if response.usage else {},
        extraction=extraction,
        requested_model=cfg.llm.extract_model,
        request_fingerprint=fingerprint,
    )

    out = cache_path(cfg, pdf_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    history = out.parent / "history" / out.stem
    if out.exists():
        _archive(out.read_text(encoding="utf-8"), history)
    content = record.model_dump_json(indent=2)
    _archive(content, history)
    temporary = out.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(out)
    return record


def letter_pdfs(cfg: Config) -> list[Path]:
    letters_dir = REPO_ROOT / cfg.backtest.letters_dir
    return sorted(p for p in letters_dir.glob("*.pdf") if "guide" not in p.stem.lower())
