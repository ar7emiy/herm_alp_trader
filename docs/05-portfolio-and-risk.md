# 05 — Portfolio, Sizing, and Exits

## Sizing — Crown's framework, applied to *our* expression

From the base guide: sizing in the letter is a **multiplier** of a standard position
(1.00 / 0.75 / 0.50 / 0.25), and a 1.00 position risks 1–2% of the tactical sleeve —
*risked*, not deployed.

```
sleeve        = 5000                           # config: sizing.sleeve (D3)
risk_at_1_00  = sleeve × 0.015 = $75           # config: sizing.risk_per_1_00
budget        = risk_at_1_00 × crown_multiplier
```

Risk is computed on **our** expression, at **our** fill, to **our** stop:

| Expression | Risk per unit |
|---|---|
| Stock / ETF (long or inverse) | `|fill − stop_in_execution_space|` per share |
| Long option | premium paid (or premium − value at the stop rule, if a tighter exit applies) |
| Debit spread | debit paid |

- `units = floor(budget / risk_per_unit)`. **Never round up.** If 0 → next candidate in doc
  04; if none → skip.
- Shares may be fractional only if whole shares give 0 and the symbol is fractionable
  (doc 02).
- Sizing is re-run at fill time with the actual price. If the fill moved risk per unit by
  more than the configured tolerance, units are recomputed before submission (the v1 bug
  where risk was sized from the letter's level is explicitly fixed).
- **Missing multiplier:** the letter didn't print one → use 0.25 and flag. Never infer
  size from conviction (base guide: conviction is independent of sizing).
- **Conviction ●●●○○ or lower** → not actionable on its own (base guide). No position
  unless the letter explicitly sizes it.

## Portfolio caps (code-enforced)

| Cap | Default | Why |
|---|---|---|
| Total open risk (sum of risk-to-stop) | 6% of sleeve ($300) | Survival: everything stopping out at once is a bad week, not a blown account |
| Risk per thesis (all positions on one thesis) | 1.5× risk_at_1_00 | A thesis with several Crown legs can't be multiplied into a large bet |
| Correlated cluster risk | 3% of sleeve | Four semis shorts are one bet, not four |
| Max concurrent positions | 6 | Monitoring and explainability load |
| Min settled-cash reserve | 10% of sleeve | T+1 settlement; avoid good-faith violations |
| Max notional per order | 40% of sleeve | Backstop against a runaway order size (not the sizing control) |

Clusters are computed from 60-day return correlation of held instruments (threshold in
config), refreshed daily. When a new position would breach a cap, it is scaled down to fit;
if that rounds to 0, it is skipped with the cap named as the reason.

## Hedge legs

Crown's book is a portfolio. Positions linked by `hedges` in the letter model, and pairs,
are sized **as a thesis**, not leg by leg:

- All legs expressible → size each normally.
- A hedge leg is **not** expressible → the remaining legs are scaled by
  `hedge_unavailable_scale` (default 0.5) and flagged "unhedged versus Crown." If the
  thesis is explicitly relative-value (the hedge *is* the bet), skip instead.
- This rule is a tier-1 deviation and is attributed separately in evaluation.

## The exit stack

Crown rarely publishes complete exits, and Crown's own exit for a position may arrive a
week late. Every position therefore carries an **exit plan** computed at entry. Exits are
evaluated continuously; **whichever fires first wins**, and stops only ever tighten.

| # | Exit | Source | Tier |
|---|---|---|---|
| 1 | **Crown stop** — price level or parsed behavioral rule, on the reference instrument | letter | 0 |
| 2 | **Crown thesis invalidation** — any `invalidation` condition in the thesis (price, event outcome, regime) | letter | 0 |
| 3 | **Crown lifecycle update** — Crown closed / cut / moved the stop in a later letter or mid-cycle update | letter | 0 |
| 4 | **Crown targets** — T1 takes half off and moves the stop to breakeven (base guide default); T2/T3 as stated | letter | 0 |
| 5 | **Volatility hard stop** — when Crown gives no usable price stop: `k_stop × ATR(14)` from entry | overlay | 2 |
| 6 | **Trailing stop** — after the position gains 1R: chandelier `k_trail × ATR(14)` from the best close | overlay | 2 |
| 7 | **Time stop** — no progress (< 0.5R) after `max(Crown horizon, N days)` | overlay | 2 |
| 8 | **Event de-risk** — before a scheduled event the position is *not* meant to hold through, cut to half or flat | overlay | 2 |
| 9 | **Instrument limits** — leveraged inverse max hold; options: exit at configured DTE, or at a % of max value | overlay | 1 |
| 10 | **Dropped-silently policy** — Crown stops mentioning an open position (doc 06) | policy | 1 |

Defaults (`k_stop = 2.0`, `k_trail = 3.0`, `N = 10` trading days) are starting points, not
tuned values. Tier-2 exits run live only if the backtest shows they help *versus the same
book with tier-0/1 exits only* (doc 09).

**Volatility sanity check on Crown's stop:** if Crown's stop is closer than `0.5 × ATR(14)`
from our fill, it is likely inside normal noise for our (later) entry. The position is
either sized on Crown's stop as-is (default, tier 0) or flagged for review — the system
does not silently widen Crown's stop.

### Where exits live

- **Broker-resident wherever possible.** Entries are submitted with their protective stop
  (bracket / OTO) so the stop survives the agent dying. Trailing and translated stops are
  updated by replacing the broker order — always to a tighter level.
- **Agent-side only when the broker can't hold it**: behavioral rules, event/regime
  conditions, options exits keyed to the underlying, fractional positions. These are
  logged as "agent-dependent" and covered by the independent watchdog (doc 07).

### Ratchet rule

A stop may move only in the risk-reducing direction. Code rejects any loosening,
including one proposed by the LLM reviewer or by a later Crown letter (a Crown stop that
moves *wider* is flagged for human review, not applied automatically).

## Deviation tiers

| Tier | Definition | Live? |
|---|---|---|
| 0 — Replicate | Crown's instrument, structure, size multiplier, stop, and targets | Yes |
| 1 — Forced deviation | Required by a constraint: instrument swap, smaller unit, level translation, hedge-leg scaling, instrument limits | Yes, always explained |
| 2 — Risk overlay | Changes size or exit timing, never direction or thesis: vol stops, trailing, time stops, event de-risk, portfolio caps beyond Crown's | Only if the backtest supports it; each overlay attributed separately |
| 3 — Own alpha | Reweighting, discarding Crown trades by our own signal, adding trades | Shadow paper only |

Every order and exit records its tier. The evaluation (doc 09) reports performance by
tier so it is always visible how far the system has drifted from Crown and whether the
drift paid.
