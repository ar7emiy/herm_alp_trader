# 03 — Letter Model (thesis-first extraction)

The extractor turns one letter into a structured model of **what Crown believes** (theses)
and **how Crown is betting on it** (positions), plus the supporting context. Trades hang
off theses so that monitoring, exits, and explanations can ask "is the *reason* for this
position still true?", not only "has price hit a number?".

## Letter anatomy (from the base guide)

Every issue follows one skeleton; recent issues renamed some sections. Map both onto the
same model and never fail on a missing or renamed section.

| Canonical section | Newer name seen | Feeds |
|---|---|---|
| What's New This Week (calendar) | — | `events` |
| Tactical Summary (trade table) | reconstructed from Open Trades | `crown_positions`, `levels` |
| Positioning This Week | — | `evidence` (positioning) |
| Notable Flow | — | `evidence` (flow) |
| Featured Trade(s) | New Trade Setups | `theses` + `crown_positions` (full structure) |
| Previous Week Trade Review | Closed Trades | `lifecycle_updates` |
| Portfolio House View | Portfolio "House" View | `house_view` |

Format drift is expected. A new section name or layout is a review flag, not an error.

## Schema

All fields not stated in the letter are `null`. Every object that carries a claim also
carries `source: {page, quote}` — the page number and a short verbatim transcription.

> **Authoritative definition:** [src/crown/letter/schema.py](../src/crown/letter/schema.py).
> The block below is the conceptual shape; the code differs in a few mechanical ways
> required by strict structured outputs: the extractor emits **letter-local** keys
> (`thesis_key`, `position_key`) and the linker assigns the stable `thesis_id` /
> `crown_position_id`; arrays like `band` and `breakevens` are split into `_low`/`_high`
> fields; and `received_at` / `extractor_version` live in the code-owned
> `ExtractionRecord` envelope, never in LLM output.

```jsonc
{
  "letter": {
    "letter_date": "YYYY-MM-DD",          // printed date
    "received_at": "ISO-8601",            // set by ingest, not the LLM
    "is_mid_cycle": false,
    "extractor_version": "string"
  },

  "theses": [{
    "thesis_id": "string",                // stable across letters (see Linking)
    "title": "Gold rolls over on firmer real yields",
    "claim": "What Crown expects to happen",
    "drivers": ["hawkish Fed", "real yields rising"],
    "evidence": [{"kind": "positioning|flow|vol|macro|technical", "detail": "...", "source": {}}],
    "horizon": "days|weeks|months",
    "invalidation": [                      // what would prove the thesis wrong
      {"kind": "price_level", "instrument": "GLD", "op": "close_above", "level": 392,
       "sessions": 1, "source": {}},
      {"kind": "event_outcome", "event_ref": "PCE", "condition": "hot print", "source": {}},
      {"kind": "narrative", "text": "sustained rotation back to AI leadership", "source": {}}
    ],
    "conviction": {"dots": 4, "raw": "●●●●○", "flag": "ok|glyph_count_mismatch"},
    "house_view_relation": "aligned|conflicts|unrelated",   // informational only, never a veto
    "source": {}
  }],

  "crown_positions": [{
    "crown_position_id": "string",        // stable across letters
    "thesis_id": "string",
    "status": "entered|conditional|closed",
    "instrument": {
      "primary": "GLD",                   // what Crown names first
      "alternatives": ["IAU"],            // e.g. "HG / CPER" → ["HG","CPER"]
      "asset_class": "equity|etf|future|fx|index|option_on_equity|option_on_future"
    },
    "structure": {
      "type": "long|short|bear_call|bull_put|iron_condor|debit_call_spread|debit_put_spread|long_call|long_put|seagull|pair|other",
      "legs": [{"action": "buy|sell", "right": "call|put|stock", "strike": 385, "expiry": "YYYY-MM-DD", "ratio": 1, "underlying": "GLD"}],
      "net_price": 3.00,                  // + debit paid, − credit received, per unit
      "max_loss_per_unit": null,          // as stated; validator recomputes from legs
      "max_gain_per_unit": null,
      "breakevens": [null, null]
    },
    "view": "bullish|bearish|neutral_range|relative_value|hedge",
    "entry": {
      "type": "entered|conditional",
      "level": null, "band": [null, null], "condition": "string",
      "triggered": true,
      "window_close": "YYYY-MM-DD|null"   // last valid entry date, if the letter implies one
    },
    "stop": {"type": "price|dollar_loss|behavioral|none", "level": null, "dollar": null,
             "rule": "string", "sessions": null},
    "targets": [{"tier": "T1", "level": null, "action": "string"}],
    "sizing_multiplier": 0.75,            // Crown's 1.00/0.75/0.50/0.25 — never inferred
    "event_dependency": "MU earnings 6/24 AMC|null",
    "management_notes": "string",
    "hedges": ["crown_position_id"],      // positions this one offsets, if the letter says so
    "source": {}
  }],

  "lifecycle_updates": [{                  // from Review / Closed Trades / Open Trades
    "crown_position_ref": "free text as written",   // linker resolves to an id
    "kind": "closed|stop_moved|target_moved|resized|rolled|still_open|invalidated",
    "detail": "string",
    "exit_level": null, "reported_pnl": "string|null",
    "source": {}
  }],

  "levels": [{"instrument": "SPX", "level": 7457, "kind": "zero_gamma|put_wall|call_wall|support|resistance|battleground|other",
              "meaning": "string", "source": {}}],
  "events": [{"name": "MU earnings", "at": "ISO-8601|null", "hits": "string",
              "affects": ["crown_position_id or ticker"], "source": {}}],
  "house_view": [{"asset_class": "Gold", "view": "Bearish", "driver": "string", "source": {}}],
  "review_flags": [{"field": "path", "reason": "string"}]
}
```

## Extraction rules (prompt) and validation rules (code)

**Extractor (prompt-level):**
- **Never fabricate.** A value not printed in the letter is `null`. Especially sizing
  multipliers, conviction, strikes, and levels.
- **Conviction glyphs.** Count glyphs. If not exactly 5, set
  `flag = "glyph_count_mismatch"` and report the literal filled count; don't guess.
- **Triggered vs not.** "Not triggered" / an unmet condition is `triggered: false`. Never
  "basically triggered."
- **Entry windows.** If entry timing is tied to an event ("ahead of Wednesday's
  earnings"), set `window_close` to the last valid date. Otherwise `null`.
- **Alternatives.** Record every instrument Crown names; don't choose.
- **Invalidation is mandatory to look for.** For each thesis, extract every stated
  condition that would make Crown wrong, typed as price / event / narrative. If none is
  stated, leave it empty — doc 05 supplies a risk overlay; the extractor does not.
- **Quote everything.** Each claim gets a page number and short verbatim quote.
- Preserve ranges and observation conventions literally. A reclaim zone does not state
  a midpoint, closing-price comparison, or session count. Leave those fields null and
  flag the missing execution interpretation; a later approved policy may resolve it.
- Option premium/risk figures are raw printed values. They do not establish contract
  dollars. Resolve multiplier, deliverable, ratio units and quote context separately.

**Validator (code):**
- Recompute spread max loss/gain and breakevens from legs and `net_price`; flag mismatches.
- Stops on the correct side of entry for the stated view.
- Dates parse and fall in a sane window relative to `letter_date`.
- Tickers resolve against a known symbol list (Alpaca assets + futures/FX roots table).
- Every `crown_position` references an existing `thesis_id`.
- Any failure → `review_flags`. The plan for an affected position is held until resolved
  (during paper soak) or skipped (live, if unresolved by the decision deadline).

Phase 0 implements only a preliminary offline subset via `crown audit <pdf>`: source
page bounds, local references, dates, basic numerical consistency, and review findings.
It does not validate ticker identity, complete option payoff arithmetic, transcription
accuracy, or eligibility. A clean report still requires page review and grants no approval.
Private page reviews bind to the exact extraction hash; superseded attempts are archived.

## Cross-letter linking

Positions persist across letters; the model must too.

1. The linker matches each extracted position to open history by instrument + structure
   + strikes/expiry + direction, falling back to an LLM-assisted match when wording
   differs ("the GLD puts" ↔ "GLD Jun26 385/375 put spread").
2. A match keeps the existing `crown_position_id`; a non-match creates a new one.
3. `lifecycle_updates` are resolved to ids the same way. Unresolved updates are flagged.
4. A previously open position that the new letter neither lists nor updates is marked
   `dropped_silently` for the reconciler (doc 06).

Thesis ids link the same way (title + instrument + direction), so a thesis can span many
letters and many positions.

## Worked example (June 22 issue, abbreviated)

```jsonc
{
  "theses": [{
    "thesis_id": "gold-bearish-2026-06",
    "title": "Gold vulnerable to hawkish Fed / firmer real yields",
    "horizon": "weeks",
    "invalidation": [{"kind": "price_level", "instrument": "GLD", "op": null,
                      "level": null, "sessions": null,
                      "text": "reclaim of $391–392 zone; comparison convention unresolved",
                      "source": {"page": 9, "quote": "Invalidation is a reclaim of the $391–$392 zone"}}],
    "house_view_relation": "aligned"
  }],
  "crown_positions": [{
    "crown_position_id": "gld-385-375-put-spread-jun26",
    "thesis_id": "gold-bearish-2026-06",
    "status": "entered",
    "instrument": {"primary": "GLD", "alternatives": [], "asset_class": "option_on_equity"},
    "structure": {"type": "debit_put_spread",
                  "legs": [{"action": "buy", "right": "put", "strike": 385, "expiry": "2026-06-26"},
                           {"action": "sell", "right": "put", "strike": 375, "expiry": "2026-06-26"}],
                  "net_price": 3.00},
    "view": "bearish",
    "entry": {"type": "entered", "level": null, "triggered": true},
    "stop": {"type": "behavioral", "rule": "reclaim of $391–392 zone"},
    "sizing_multiplier": null
  }]
}
```

The USDJPY discussion in this issue does not authorize reactivating the original
157.50 short. "No trade" and a changed setup must remain distinct from a still-valid
pending trigger. Preserve that distinction in review until the lifecycle model can
represent it explicitly. The inherited v1 fixture is not an audited transcription.
