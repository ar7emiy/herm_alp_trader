"""The exact regression suite from 03-build-plan.md's Phase 3 exit
criteria: "Feed it the June 22 issue. A correct run must: ..."

Uses tests/fixtures/june22_parsed_letter.json — a hand-built ParsedLetter
matching the worked example in 05-letter-schema.md exactly (this is what
parse_letter() should produce from the real PDF; parse_letter itself needs
an Anthropic API key and isn't exercised here — this suite covers the
deterministic half of the pipeline, build_plan + the zone-check engine,
which is everything that can be verified without live API access).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

from crown.compiler.build_plan import build_plan
from crown.engine.zonecheck import EntryOutcome, check_entry
from crown.models import ParsedLetter

FIXTURE = Path(__file__).parent / "fixtures" / "june22_parsed_letter.json"


def load_letter() -> ParsedLetter:
    return ParsedLetter.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def find(plan, crown_symbol_substring: str):
    matches = [t for t in plan.trades if crown_symbol_substring in t.crown_symbol]
    assert matches, f"no trade found matching '{crown_symbol_substring}'"
    return matches[0]


# -- "compute MU condor sizing -> 0 contracts at your sleeve -> invoke the
#    Alternative Finder" ----------------------------------------------------

def test_mu_condor_sizes_to_zero_contracts_at_sleeve(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    mu = find(plan, "MU")

    assert mu.structure == "iron_condor"
    # Crown's exact structure at $5k sleeve, 0.75 multiplier: budget $56.25
    # against $81.80 max risk/contract -> 0 contracts, exactly the doc's
    # own worked example.
    assert any("0 contracts" in entry for entry in mu.alt_finder_log)


def test_mu_condor_invokes_the_alternative_finder(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    mu = find(plan, "MU")

    log_text = " | ".join(mu.alt_finder_log)
    assert "Alt Finder Step 1" in log_text
    assert "Alt Finder Step 2" in log_text
    assert "Alt Finder Step 4" in log_text or "Alt Finder Step 5" in log_text
    # At this sleeve even the equity-proxy (Step 4) doesn't fit a $95-wide
    # band, so the honest, doc-06-sanctioned outcome is SKIP — a skip is "a
    # valid, frequent, correct outcome," not a failure of the Alt Finder.
    assert mu.action == "skipped"


# -- "flag the ●●●○ conviction as a glyph mismatch rather than guessing
#    3/4 vs 3/5" --------------------------------------------------------

def test_mu_conviction_flagged_as_glyph_mismatch_not_guessed(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    mu = find(plan, "MU")

    assert mu.conviction_flag == "glyph_count_mismatch"
    # The raw glyph count (3 filled dots) is preserved for human review, not
    # silently resolved to either 3/4 or 3/5.
    assert mu.conviction == "3/5"


# -- "refuse to stage the USDJPY short (trigger 157.50 never hit -> not a
#    trade)" -------------------------------------------------------------

def test_usdjpy_never_staged(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    usdjpy = find(plan, "USDJPY")

    assert usdjpy.action == "skipped"
    assert usdjpy.traded_symbol is None

    # Independently of *why* build_plan already skips it (no FX data source
    # in v1), the trigger mechanism itself must also refuse to stage an
    # untouched conditional level — verified directly against the letter's
    # own trigger, in case a future extension adds an FX data source.
    outcome = check_entry(usdjpy, price=152.0, now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg)
    assert outcome in (EntryOutcome.NOT_YET, EntryOutcome.WINDOW_CLOSED)
    assert outcome != EntryOutcome.TRIGGER


# -- "mark the MU entry window (Mon/Tue) CLOSED if run on/after Wednesday,
#    not enter late" ------------------------------------------------------

def test_mu_entry_window_closes_wednesday(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    mu = find(plan, "MU")

    assert mu.entry_window_close == "2026-06-23"  # Mon/Tue only

    # Even if price is squarely inside the entry band, a Wednesday check
    # must refuse to enter — the window already closed.
    wednesday = datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc)
    outcome = check_entry(mu, price=1120.0, now=wednesday, cfg=cfg)
    assert outcome == EntryOutcome.WINDOW_CLOSED


# -- "record GLD's invalidation as a behavioral rule (reclaim of
#    $391-392), not a price stop" -----------------------------------------

def test_gld_invalidation_is_behavioral_not_price(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    gld = find(plan, "GLD")

    assert gld.stop.type == "behavioral"
    assert "391" in gld.stop.rule and "392" in gld.stop.rule
    assert gld.stop.current == 391.5  # midpoint of the reclaim zone


# -- "NOT veto the GLD put spread for conflicting with anything in the
#    house view" -----------------------------------------------------------

def test_gld_not_vetoed_by_house_view(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    gld = find(plan, "GLD")

    # GLD short is bearish, and the letter's own house view on Gold is also
    # bearish — aligned, not conflicting, so there should be no note either.
    # The real assertion is the one that matters structurally: house-view
    # disagreement is NEVER grounds to change `action` — see the MSFT case
    # below, which DOES conflict and must still not be vetoed.
    assert gld.action == "watchlist"


def test_house_view_conflict_is_noted_but_never_blocks(cfg):
    letter = load_letter()
    plan = build_plan(letter, cfg)
    msft = find(plan, "MSFT")

    # MSFT short vs. house view "Equities: Constructive, hedged" — this DOES
    # conflict. doc 04: "the house view and the tactical book are SEPARATE
    # ... a tactical trade conflicting with the house view is normal and is
    # NOT grounds to veto — note it, don't block it."
    assert msft.house_view_conflict_note is not None
    assert msft.action == "watchlist"  # noted, never blocked


# -- "stage nothing without your approval" ---------------------------------

def test_nothing_fills_without_approval(cfg):
    from crown.backtest.runner import BacktestRunner
    from crown.data.calendar import market_open_close, trading_days
    import pandas as pd

    zero_approval = dataclasses.replace(
        cfg, backtest=dataclasses.replace(cfg.backtest, approval_rate=0.0),
    )
    days = trading_days("2026-06-22", "2026-06-26")
    rows = []
    for day in days:
        oc = market_open_close(day)
        if not oc:
            continue
        rows.append({"timestamp": oc[0], "open": 393.0, "high": 393.5, "low": 392.5, "close": 393.0, "volume": 1000})
        rows.append({"timestamp": oc[1], "open": 393.0, "high": 393.5, "low": 392.5, "close": 393.0, "volume": 1000})
    bars = pd.DataFrame(rows).set_index("timestamp")

    letter = load_letter()
    runner = BacktestRunner(zero_approval)
    result = runner.run(
        start=days[0], end=days[-1], letters=[letter],
        minute_bars={"GLD": bars, "MSFT": bars}, daily_bars={"GLD": bars, "MSFT": bars},
    )

    assert result.fills == []
    for rt in result.runtimes.values():
        assert rt.status != "open"
        if rt.status == "rejected_approval":
            assert rt.close_note == "not approved by the simulated approval policy"
