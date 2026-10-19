# 04 — Substitution Engine

> 2026-10-19 modeling clarification: credit/debit labels alone do not determine payoff
> fidelity. Same-expiry, same-strike verticals can have economically equivalent terminal
> payoffs through put-call parity, subject to prices, carry/dividends, exercise risk and
> account feasibility. Doc 13 requires exact scenario/payoff checks before substitution;
> the coarse family labels and examples below are not sufficient eligibility rules.

For each Crown position the engine answers: **what is the most faithful expression of this
bet that a $5k cash account can actually hold — or should we skip it?** The LLM proposes
candidates; code filters, scores, and chooses; the result is recorded with every rejected
alternative and the reason.

## Step 1 — Describe the bet, not the trade

Before looking for substitutes, reduce Crown's position to an **exposure profile**. Two
expressions are comparable only along these dimensions:

| Dimension | Values | Example |
|---|---|---|
| Reference instrument | what Crown's levels are quoted on | SPX, HG, USDJPY, MU |
| Direction | long / short / neutral / relative | bearish gold → short |
| Payoff shape | `linear` · `capped_directional` (debit spread) · `long_convexity` (long option) · `range_short_vol` (credit spread, condor) · `relative` (pair) | MU condor → range_short_vol |
| Horizon | days / weeks / months, plus expiry if any | |
| Event dependence | the event the bet is about, if any | "MU earnings 6/24" |
| Vol stance | long vol / short vol / neutral | condor → short vol |

The **shape** matters as much as direction. A bear call spread says "it won't go above X"
— it profits if price is flat. An inverse ETF says "it will go down" — it loses if price is
flat. Same direction, different bet. The engine must say so.

## Step 2 — Generate candidates

Candidate families, tried in this order (the LLM proposes concrete symbols/strikes inside
each family; code fills in market data):

1. **Exact** — Crown's own structure, if the account can hold it and it sizes to ≥1 unit.
2. **Same underlying, smaller unit** — narrower strikes, fewer legs, fractional shares.
3. **Same exposure, different wrapper** — Crown's named alternative ("HG / CPER" → CPER),
   the ETF for an index (SPX → SPY), the currency ETF for an FX pair (USDJPY short → long FXY).
4. **Inverse / owned-bearish** — −1x inverse ETF, long put, debit put spread.
5. **Correlated proxy** — sector ETF for a single name, a cheaper related underlying for
   options (MU → SMH/SOXX options).
6. **Skip** — always a candidate, scored like the rest.

### Common re-expressions in a cash account

| Crown structure | Shape | Feasible re-expressions (if capability allows) | What is lost |
|---|---|---|---|
| Long stock/ETF/future | linear | same (whole or fractional), ETF for future | futures leverage; small tracking error |
| Short stock/ETF/future | linear short | −1x inverse ETF; long put; debit put spread | inverse decay; option theta/premium |
| Debit spread | capped_directional | same, narrower width; single long option | cap removed → higher cost per unit of risk |
| Bear call / bull put (credit) | range_short_vol (one-sided) | debit spread in the same direction; directional proxy | **no longer paid to wait** — flat price now loses; flag shape change |
| Iron condor (credit) | range_short_vol | long butterfly / iron butterfly bought for a debit centered in the range (needs spread capability) | much narrower profit zone; different vol exposure; usually **skip** if no spreads |
| Long call / put | long_convexity | same on a cheaper underlying, or narrower debit spread | convexity partially capped |
| Seagull | mixed | usually skip | — |
| Pair (long A / short B) | relative | long A + long put or −1x inverse on B; or long A only, scaled | relative bet becomes partly directional; flag |

## Step 3 — Hard feasibility filters (code)

A candidate is discarded, with the reason logged, if any of these fail:

- Account capability (cash; options level; fractionable; allowed order types).
- Sizes to ≥1 unit within the risk budget after rounding **down** (doc 05).
- Liquidity: minimum average daily volume; for options, open interest and
  bid/ask spread ≤ configured % of mid.
- Options: ≥3 trading days to expiry (Crown's guide) and expiry not before the thesis
  horizon's end unless Crown's own position expires sooner.
- Direction and shape exposure compatible with the thesis (a candidate that profits when
  Crown's thesis fails is never eligible, even as a "hedge").
- Leveraged inverse ETFs only if the expected holding period is within the configured
  maximum.

## Step 4 — Score (code)

```
score = fidelity − cost_penalty
fidelity = w_corr · corr(candidate, reference)          # 60-day daily returns, sign-adjusted
         + w_shape · shape_match[crown_shape][cand_shape]  # fixed lookup table, 0..1
         + w_event · event_exposure_match                  # holds through the same event?
         + w_horizon · horizon_fit                         # expiry/decay vs thesis horizon
cost_penalty = expected spread cost + expected decay (inverse/theta) + tracking error
               + granularity miss (|achievable risk − budget| / budget)
```

- Weights live in `config.yaml`. Keep them few and coarse — the sample is small (doc 09).
- A candidate below `min_fidelity` is not eligible even if it is the only one. Then the
  answer is **skip**.
- Ties go to the simpler instrument (stock/ETF over options; fewer legs over more).

## Step 5 — Translate levels

Crown's entry, stop, target, and invalidation levels are on the reference instrument. The
chosen expression needs them in its own price terms for broker-side orders.

| Relationship | Translation | Example |
|---|---|---|
| Same economic exposure, scaled | `level_cand = level_ref × (px_cand / px_ref)` refreshed daily | SPX 7457 → SPY ≈ 745.x |
| Inverse | move in reference → opposite move in candidate, scaled by leverage | SPX stop 7600 (above) → SH stop below current SH price |
| Reciprocal (FX) | `FXY ∝ 1 / USDJPY` | USDJPY 157.50 → FXY level via current ratio |
| Correlated proxy | beta-scaled move from current prices, with a widened tolerance | MU level → SMH level (low confidence) |
| Option | monitor the **underlying** condition; the option's own price is not the trigger | A reclaim zone requires an approved comparison convention; never invent its midpoint |

Rules: thesis invalidation is always evaluated on the reference instrument when data
exists (signal space). Translated levels drive broker-side protective orders (execution
space) and are refreshed as prices move. Every translation records its method and
confidence.

## Output: the expression record

```jsonc
{
  "crown_position_id": "...",
  "exposure_profile": {"reference": "GLD", "direction": "short", "shape": "capped_directional", ...},
  "chosen": null,
  "planning_status": "held",
  "proposal": {"symbol": "GLD", "structure": "debit_put_spread", "legs": [...], "score": 0.82,
             "preserved": ["direction", "defined risk", "expiry"],
             "lost": ["strike width narrowed 10→5"]},
  "rejected": [
    {"candidate": "GLL (−2x gold)", "reason": "leveraged inverse; thesis horizon exceeds max hold"},
    {"candidate": "GLD 385/375 as written", "reason": "$3 debit × verified 100 multiplier = $300 premium at risk before fees; exceeds budget"}
  ],
  "level_translation": [],
  "unresolved": ["Crown reclaim zone has no stated close/trade comparison; plan held pending approved interpretation"],
  "deviation_tier": 1
}
```

This record is the backbone of the "why" in every memo and report (doc 08).

## Worked examples

- **MU iron condor (June 22)** — shape `range_short_vol`, event-dependent (earnings). Exact
  structure: 0 contracts at 0.75 sizing. Cash account can't sell premium. If spreads are
  allowed: a small long iron butterfly centered in Crown's band is eligible but flagged
  "narrower profit zone, different vol bet". If not: **skip**. A directional MU or SMH
  trade is *not* eligible — Crown's bet was "MU stays in a range," not a direction.
- **USDJPY discussion (June 22)** — the original short was not taken and the next setup
  changed. Do not revive the old trigger. FXY is only a potential wrapper for a separately
  valid directional short setup with verified reference data and an approved plan.
- **Copper "HG / CPER" long** — Crown names CPER; exact-wrapper candidate, identity-ish
  translation via ratio, tier 0/1.
- **SPX bear call spread** — shape is one-sided `range_short_vol`. SH (−1x) matches
  direction but not shape (loses if flat). Debit put spread on SPY matches direction with
  defined risk but also needs a move. Both flagged "shape changed: needs a move instead of
  getting paid to wait." Fidelity likely below threshold → skip unless the thesis text is
  explicitly directional.
