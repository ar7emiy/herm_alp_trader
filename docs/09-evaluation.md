# 09 — Evaluation

The question evaluation must answer: **are we following Crown, and where we deviate, is it
costing or helping?** Raw P&L alone can't answer that.

## Three books

| Book | What it is | How it's priced |
|---|---|---|
| **Crown replica** | Crown's positions exactly as written, at Crown's stated entries, sizing multipliers, stops, and exits (including exits announced in later letters) — scaled to the same risk unit | Market data where available; Crown's own reported outcomes (Review / Closed Trades) where we can't price the instrument (futures, FX, options without history). Each position is tagged `priced` or `crown_reported`. |
| **Feasible replica** | The same, but only positions we *could* express, at our earliest feasible entry, with tier-0/1 rules only | Market data |
| **Actual** | What the system did (backtest or live), including tier-2 overlays | Fills |

## Shortfall attribution

```
Crown replica − Actual =
    coverage      (Crown positions we skipped entirely, by binding constraint)
  + timing        (our entry vs Crown's entry: lag, no-chase skips)
  + substitution  (our instrument/structure vs Crown's, same timing)
  + sizing        (rounding, caps, hedge-leg scaling, missing multipliers)
  + overlays      (each tier-2 overlay separately: vol stop, trailing, time stop, event de-risk)
  + execution     (spreads, slippage)
```

Each component is computed position by position and summed. The overlay line is the one
that answers "did our own risk management help?" — reported per overlay, with and without
it, so a harmful overlay is visible and can be switched off.

## Fidelity metrics

- **Coverage** — % of Crown positions (and % of Crown's risk units) we expressed.
- **Tracking** — correlation of weekly P&L between Actual and Crown replica; tracking error.
- **Direction agreement** — share of days our net exposure per thesis had the same sign
  as Crown's.
- **Exit agreement** — for positions both books held: did we exit before, with, or after
  Crown, and what did that cost or save?

## Backtest rules

- **Point-in-time.** A letter is visible only after its `received_at` (doc 02). For the
  historical set, receipt time is assumed to be the next market open after the printed
  date unless known; the assumption is recorded in the report.
- **Extraction is frozen.** Backtests use cached extractions from a fixed extractor
  version; re-extracting changes the experiment and must be versioned.
- **No hindsight in the LLM.** Escalation reviews in backtests see only data up to the
  simulated time. If an LLM review is not reproducible offline, the backtest uses a
  deterministic stand-in and says so.
- **Conservative fills.** Entries at the bar close after the trigger, stops at the stop or
  worse on gaps, targets at the target never better; options at the far side of the
  quoted spread when quotes exist.
- **Options without history** are marked approximate in every table they touch.
- **Same engine as live.** The backtest drives the real planner, monitors, and order
  gateway through simulated adapters.

## Avoiding overfitting

17 letters is tens of positions, not hundreds.

- Few parameters, coarse grids: e.g. `k_stop ∈ {1.5, 2, 3}`, `k_trail ∈ {2, 3, 4}`.
- **Walk-forward:** choose on the first ~10 letters, report on the rest untouched. Keep
  adding letters to the out-of-sample set as they arrive.
- Prefer an overlay only if it helps in both halves and doesn't reduce coverage or
  fidelity materially. "Helps on average, hurts tracking a lot" is a tier-3 idea, not tier 2.
- Report confidence honestly: with this sample, most differences will not be significant.
  The paper soak is the real test.

## Paper-soak success criteria (gate to live)

- 4+ consecutive weeks with no guardrail-relevant errors.
- Extraction audit ≥ 98% field accuracy on hand-checked letters; zero errors in
  levels/strikes/sizing.
- Every exit in the period explainable from its decision record.
- Actual vs feasible replica shortfall explained by attributed components, with no large
  "unexplained" residual.
