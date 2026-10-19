"""Phase 0 letter inventory (docs/10-build-plan.md).

Aggregates cached extractions into the numbers the design decisions depend
on: what Crown actually trades, how much of it a cash account can hold
as-is, and how often the letter supplies stops, targets, invalidations, and
sizing. Pure Python over cached JSON — no API calls.

Cross-letter persistence here is a rough heuristic (instrument + structure +
view). Proper linking with stable ids is Phase 1; numbers from the heuristic
are labeled as such in the report.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from crown.config import Config
from crown.letter.extract import letter_pdfs, load_cached
from crown.letter.schema import CrownPosition, ExtractionRecord

# Structures a cash account cannot hold as written (docs/02-constraints.md A).
NEEDS_MARGIN = {"short", "bear_call", "bull_put", "iron_condor", "seagull", "pair"}
# Asset classes Alpaca does not offer directly (docs/02-constraints.md B).
NOT_ON_ALPACA = {"future", "fx", "index", "option_on_future"}


def load_all(cfg: Config) -> tuple[list[ExtractionRecord], list[Path]]:
    records, missing = [], []
    for pdf in letter_pdfs(cfg):
        rec = load_cached(cfg, pdf)
        if rec is None:
            missing.append(pdf)
        else:
            records.append(rec)
    records.sort(key=lambda r: r.extraction.letter_date)
    return records, missing


def needs_substitution(p: CrownPosition) -> list[str]:
    reasons = []
    if p.structure.type in NEEDS_MARGIN:
        reasons.append(f"structure '{p.structure.type}' needs margin/shorting")
    if p.instrument.asset_class in NOT_ON_ALPACA:
        reasons.append(f"asset class '{p.instrument.asset_class}' not on Alpaca")
    return reasons


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def _key(p: CrownPosition) -> tuple[str, str, str]:
    return (p.instrument.primary.upper(), p.structure.type, p.view)


def build_report(cfg: Config) -> str:
    records, missing = load_all(cfg)
    lines: list[str] = ["# Phase 0 — Letter inventory", ""]
    lines += ["**Unapproved research inventory.** Counts come from model extractions; "
              "they are not verified trade inputs. Run `crown audit <pdf>` and review "
              "every page before using the data in planning. A clean automated check "
              "cannot certify transcription accuracy.", ""]
    if missing:
        lines += [f"**Not yet extracted ({len(missing)}):** " + ", ".join(p.name for p in missing), ""]
    if not records:
        lines.append("No extractions found. Run `crown extract --all` first.")
        return "\n".join(lines)

    # --- per letter --------------------------------------------------------
    lines += ["## Per letter", "",
              "| Letter date | File | Mid-cycle | Theses | Positions (entered/cond/closed) | Lifecycle updates | Review flags | Tokens in/out |",
              "|---|---|---|---|---|---|---|---|"]
    for r in records:
        x = r.extraction
        st = Counter(p.status for p in x.crown_positions)
        u = r.usage or {}
        lines.append(
            f"| {x.letter_date} | {r.pdf_name} | {'yes' if x.is_mid_cycle else ''} | {len(x.theses)} | "
            f"{len(x.crown_positions)} ({st['entered']}/{st['conditional']}/{st['closed']}) | "
            f"{len(x.lifecycle_updates)} | {len(x.review_flags)} | "
            f"{u.get('input_tokens', '?')}/{u.get('output_tokens', '?')} |"
        )
    lines.append("")

    # --- series gaps -------------------------------------------------------
    dates = [date.fromisoformat(r.extraction.letter_date) for r in records
             if not r.extraction.is_mid_cycle]
    gaps = [(a, b) for a, b in zip(dates, dates[1:]) if (b - a).days > 7]
    if gaps:
        lines += ["## Gaps in the weekly series", ""]
        lines += [f"- {a} → {b} ({(b - a).days} days)" for a, b in gaps]
        lines.append("")

    # --- positions ---------------------------------------------------------
    positions = [p for r in records for p in r.extraction.crown_positions]
    theses = [t for r in records for t in r.extraction.theses]
    n = len(positions)
    lines += [f"## Positions across all letters ({n} position rows, {len(theses)} thesis rows)", "",
              "Rows are per letter: a position listed in 5 letters counts 5 times here. "
              "Unique positions (heuristic) are below.", ""]

    def table(title: str, counter: Counter) -> None:
        lines.extend([f"**{title}**", "", "| Value | Count |", "|---|---|"])
        lines.extend(f"| {k} | {v} |" for k, v in counter.most_common())
        lines.append("")

    table("By asset class", Counter(p.instrument.asset_class for p in positions))
    table("By structure", Counter(p.structure.type for p in positions))
    table("By view", Counter(p.view for p in positions))
    table("Stop type", Counter(p.stop.type for p in positions))

    with_targets = sum(1 for p in positions if any(t.level is not None for t in p.targets))
    with_mult = sum(1 for p in positions if p.sizing_multiplier is not None)
    with_alts = sum(1 for p in positions if p.instrument.alternatives)
    with_hedges = sum(1 for p in positions if p.hedges_position_keys)
    with_window = sum(1 for p in positions if p.entry.window_close)
    untriggered = sum(1 for p in positions if not p.entry.triggered)
    lines += [
        "**How much the letter specifies**", "",
        f"- Price-level stop stated: {_pct(sum(1 for p in positions if p.stop.type == 'price'), n)}",
        f"- Any stop stated: {_pct(sum(1 for p in positions if p.stop.type != 'none_stated'), n)}",
        f"- At least one target level: {_pct(with_targets, n)}",
        f"- Sizing multiplier printed: {_pct(with_mult, n)}",
        f"- Instrument alternatives named: {_pct(with_alts, n)}",
        f"- Linked as a hedge of another position: {_pct(with_hedges, n)}",
        f"- Entry window tied to an event/date: {_pct(with_window, n)}",
        f"- Not triggered (conditional, waiting): {_pct(untriggered, n)}",
        f"- Theses with ≥1 explicit invalidation condition: "
        f"{_pct(sum(1 for t in theses if t.invalidation), len(theses))}",
        "",
    ]
    table("Thesis invalidation condition kinds",
          Counter(c.kind for t in theses for c in t.invalidation))
    table("Conviction flags", Counter(t.conviction.flag for t in theses))

    # --- feasibility in a cash account ---------------------------------------
    unique: dict[tuple[str, str, str], CrownPosition] = {}
    first_seen: dict[tuple[str, str, str], str] = {}
    for r in records:
        for p in r.extraction.crown_positions:
            k = _key(p)
            unique.setdefault(k, p)
            first_seen.setdefault(k, r.extraction.letter_date)
    sub_needed = {k: needs_substitution(p) for k, p in unique.items()}
    n_sub = sum(1 for v in sub_needed.values() if v)
    lines += [f"## Unique positions (heuristic: instrument + structure + view): {len(unique)}", "",
              f"- No structural exclusion in this screen (account/data/sizing unverified): "
              f"{_pct(len(unique) - n_sub, len(unique))}",
              f"- Need substitution or skip: {_pct(n_sub, len(unique))}", "",
              "| First seen | Instrument | Alternatives | Structure | View | Why it can't be held as written |",
              "|---|---|---|---|---|---|"]
    for k in sorted(unique, key=lambda k: first_seen[k]):
        p = unique[k]
        lines.append(f"| {first_seen[k]} | {p.instrument.primary} | {', '.join(p.instrument.alternatives)} | "
                     f"{p.structure.type} | {p.view} | {'; '.join(sub_needed[k]) or '—'} |")
    lines.append("")

    # --- lifecycle ---------------------------------------------------------
    updates = [u for r in records for u in r.extraction.lifecycle_updates]
    table(f"Lifecycle updates ({len(updates)})", Counter(u.kind for u in updates))

    dropped: list[str] = []
    for prev, cur in zip(records, records[1:]):
        cur_keys = {_key(p) for p in cur.extraction.crown_positions}
        mentioned = " ".join(u.position_ref.upper() + " " + u.detail.upper()
                             for u in cur.extraction.lifecycle_updates)
        for p in prev.extraction.crown_positions:
            if p.status != "entered" or _key(p) in cur_keys:
                continue
            if p.instrument.primary.upper() in mentioned:
                continue
            dropped.append(f"- {p.instrument.primary} {p.structure.type} ({p.view}): in "
                           f"{prev.extraction.letter_date}, absent from {cur.extraction.letter_date} "
                           "with no update mentioning it")
    lines += [f"**Possibly dropped silently (heuristic): {len(dropped)}**", ""] + dropped + [""]

    # --- review flags ------------------------------------------------------
    flags = [(r.extraction.letter_date, f) for r in records for f in r.extraction.review_flags]
    if flags:
        lines += [f"## Extractor review flags ({len(flags)})", ""]
        lines += [f"- {d}: `{f.field}` — {f.reason}" for d, f in flags]
        lines.append("")

    # --- cost --------------------------------------------------------------
    tin = sum((r.usage or {}).get("input_tokens", 0) for r in records)
    tout = sum((r.usage or {}).get("output_tokens", 0) for r in records)
    lines += ["## Extraction cost", "",
              f"- Model(s): {', '.join(sorted({r.model for r in records}))}",
              f"- Tokens: {tin:,} input / {tout:,} output across {len(records)} letters", ""]
    return "\n".join(lines)


def write_report(cfg: Config) -> Path:
    out = cfg.paths.reports / "phase0-inventory.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(cfg), encoding="utf-8")
    return out
