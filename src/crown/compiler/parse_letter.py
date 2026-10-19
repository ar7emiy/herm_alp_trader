"""PDF -> ParsedLetter via one structured-output Claude call.

This is the only place in the pipeline that touches an LLM for a given
letter. Everything downstream (build_plan.py, the backtest engine) is pure
Python operating on the resulting JSON, so a letter is compiled once and
replayed for free. Results are cached to disk keyed by the PDF filename.

Parsing rules enforced via the prompt (05-letter-schema.md, 04-system-prompt.md):
  - Map both letter layouts (Featured Trade/Tactical Summary/Previous Week
    Review, and the newer New Trade Setups/Open Trades/Closed Trades) onto
    the one schema.
  - Conviction is normalized to n/5; if the glyph count isn't 5, flag
    glyph_count_mismatch rather than guessing 3/4 vs 3/5.
  - Never fabricate a value. A field the letter doesn't state is left null.
  - "Not triggered" is extracted as entry_trigger.triggered = false, exactly
    as written — no inference that it "basically happened."
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from crown.config import REPO_ROOT, Config, anthropic_api_key, load_config
from crown.models import ParsedLetter

SYSTEM_PROMPT = """\
You are the parsing stage of a trading-letter compiler. You read one issue of
The Crown Macro Letter (a PDF) and extract it into a structured schema. You do
not judge, size, or validate trades here — that happens in a later,
deterministic stage. Your only job is faithful, literal extraction.

FORMAT DRIFT — the letter has two known layouts; map both onto the same
output schema:
  Canonical:  Featured Trade(s) | Tactical Summary | Previous Week Trade Review
  Newer:      New Trade Setups  | Open Trades       | Closed Trades
Featured Trade(s) <-> New Trade Setups. Tactical Summary <-> reconstruct from
Open Trades if the canonical table is absent. Previous Week Review <-> Closed
Trades. Never fail because a section has the newer name or is absent —
reconstruct what you can and leave the rest empty.

CONVICTION — dots represent 0-5. Count the glyphs in the raw rating. If the
count is exactly 5, set conviction_flag="ok" and conviction as "n/5". If the
glyph count is NOT 5 (e.g. "●●●○" is 4 glyphs), do NOT guess whether a glyph
was dropped or the letter used a 0-4 scale — set conviction_flag=
"glyph_count_mismatch" and report conviction using the literal count over 5
(e.g. "3/5" for 3 filled glyphs) so a human can review it, but the flag is
what downstream code acts on, not your guess.

NEVER FABRICATE. If a number, level, or field is not stated in the letter,
leave it null. This applies especially to sizing_multiplier and conviction.
Do not compute or estimate a value the letter doesn't give you.

TRIGGERED VS NOT. "Not triggered" / a conditional setup whose level the price
hasn't reached is entry_trigger.triggered=false. This is NOT the same as
triggered=true with a note — extract exactly what the letter states about
whether the level has actually been touched.

ENTRY WINDOW. If the trade's entry text ties timing to an upcoming event
("MU earnings Wed 6/24 after close", "entry only ahead of Thursday's CPI"),
set entry_window_close to the specific date (YYYY-MM-DD) after which entry is
no longer valid per the letter's own no-chase doctrine — typically the last
trading day before the event. If no such constraint is stated, leave it null.
Do not invent a window where the letter doesn't imply one.

WHITELIST INPUTS. Extract every instrument_alternatives entry Crown gives
(e.g. "HG / CPER" -> ["HG","CPER"]) — do not pick one; a later stage decides
which leg is actually tradable.

The letter_date is the date this issue was published or is dated for (state
it as YYYY-MM-DD). Set is_mid_cycle=true if this is explicitly a mid-cycle
update rather than the regular weekly issue.
"""

USER_PROMPT = (
    "Extract this Crown Macro Letter issue into the schema. Follow the "
    "system instructions exactly, especially on conviction glyph-counting, "
    "never fabricating values, and the triggered/not-triggered distinction."
)


def _cache_path(cfg: Config, pdf_path: Path) -> Path:
    return cfg.paths.parsed_letters / f"{pdf_path.stem}.json"


def parse_letter(
    pdf_path: str | Path,
    cfg: Config | None = None,
    force: bool = False,
) -> ParsedLetter:
    """Parse one letter PDF into a ParsedLetter, using the on-disk cache
    unless force=True. Costs one Claude API call on a cache miss."""
    cfg = cfg or load_config()
    pdf_path = Path(pdf_path)
    cache_path = _cache_path(cfg, pdf_path)

    if cache_path.exists() and not force:
        return ParsedLetter.model_validate_json(cache_path.read_text(encoding="utf-8"))

    client = anthropic.Anthropic(api_key=anthropic_api_key())
    pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    response = client.messages.parse(
        model=cfg.model.id,
        max_tokens=8192,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
        output_format=ParsedLetter,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude refused to parse {pdf_path.name}: {response.stop_details}")

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(f"Structured output parsing failed for {pdf_path.name}")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    return parsed


def parse_all_letters(cfg: Config | None = None, force: bool = False) -> list[ParsedLetter]:
    cfg = cfg or load_config()
    letters_dir = REPO_ROOT / cfg.backtest.letters_dir

    pdfs = sorted(p for p in letters_dir.glob("*.pdf") if "guide" not in p.stem.lower())
    parsed_letters = [parse_letter(p, cfg=cfg, force=force) for p in pdfs]
    parsed_letters.sort(key=lambda pl: pl.letter_date)
    return parsed_letters
