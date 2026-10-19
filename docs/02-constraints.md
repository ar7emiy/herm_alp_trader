# 02 — Constraints

> 2026-10-19 verification update: see [doc 14](14-robinhood-and-data.md) for current
> sources. Treat cash-only as the required operating policy, not proof of the broker's
> legal classification. Do not infer account type from multiplier alone. Replace old PDT
> assumptions with current broker restrictions; the probe still reads a deprecated field.
> Fractional stops are documented with DAY time in force; bracket/OCO and renewals require
> paper verification. The older rows below are requirements/assumptions to check, not a
> verified capability matrix.

Every reason the system cannot replicate Crown 1:1, what it implies, and how the design
handles it. Items marked **VERIFY** are assumptions about Alpaca or the account that
Phase 0 (doc 10) must confirm against the real account before anything is built on them.

## A. Account

| Constraint | Implication | Handling |
|---|---|---|
| **Cash account** (D2) | No short selling. No borrowing. No credit spreads, condors, or naked option sales (all need margin/collateral beyond what a cash account provides). | Bearish = own something that rises when the target falls (inverse ETF, long put, debit put spread). Credit structures are re-expressed or skipped (doc 04). |
| **T+1 settlement** | Sale proceeds are unsettled until the next business day. Buying with unsettled funds and selling before they settle is a *good-faith violation*; repeated violations restrict the account. | Order gateway buys only with **settled cash**. Tracks settled vs unsettled per day. Guardrail, not a preference. |
| **Pattern-day-trader rule** | Applies to margin accounts under $25k. Does **not** apply to a cash account. | No PDT logic needed — but the settled-cash rule above does the equivalent job. |
| **$5,000 sleeve** (D3) | 1.00 position = $75 risk; 0.25 = ~$19. Many Crown structures have a per-unit max loss above the budget (e.g. an $81.80-risk condor at 0.75 sizing → 0 contracts). | Substitution engine looks for smaller-granularity expressions; rounding rules in doc 05. Skip is a valid result. |
| **Options approval level** — **VERIFY** | Unknown which levels Alpaca grants a cash account. Assume worst case: long calls/puts only; spreads possibly unavailable without margin. | `capabilities` block in config, populated by the Phase 0 probe. The substitution engine only proposes structures the account can actually trade. |

## B. Instruments

| Constraint | Implication | Handling |
|---|---|---|
| **Alpaca universe**: US equities, ETFs, US equity options (**VERIFY** level), crypto. No futures, no spot FX, no international listings, no index products. | Crown's futures (HG, CL, GC, SI), FX (USDJPY, EURUSD) and index (SPX, NDX) views need proxies. | ETF proxies: CPER (copper), USO/BNO (oil), GLD/IAU (gold), SLV (silver), FXY/FXE/UUP/UDN (currencies), SPY/QQQ/IWM (indexes). Tracking error is measured and scored (doc 04). |
| **Inverse ETFs** | Clean −1x products exist mainly for broad indexes (e.g. SH, PSQ, DOG, RWM) and a few sectors/rates; most sector and commodity inverses are −2x/−3x. All reset daily → path decay in choppy markets; worse for longer holds and higher leverage. | Prefer −1x. A leveraged inverse is allowed only for short horizons, sized on its *effective* exposure, with a max holding period (doc 05). Decay is a scored cost. |
| **Options contract granularity** | One contract = 100 shares. A single at-the-money option on a high-priced name can exceed the whole risk budget. | Narrower strikes, cheaper underlyings (ETF options instead of single-name), or equity expression. Never round *up* a contract count. |
| **Fractional shares** — **VERIFY** per symbol | Fractional orders exist for many US equities/ETFs, but not every symbol is fractionable, and order types/time-in-force for fractional orders are restricted (likely no bracket/OCO). | Prefer whole shares when risk budget allows so broker-side stops work. Use fractional only when whole shares can't express the size; such positions get an agent-side stop *and* a protective alert (doc 05). |
| **Prediction markets** | Not on Alpaca. | Out of scope v1 (D10). |

## C. Information timing

| Constraint | Implication | Handling |
|---|---|---|
| **Weekly cadence** (+ occasional mid-cycle letters) | A thesis can break mid-week; Crown may adjust in a video or not at all until next Monday. | The system monitors Crown's own invalidation conditions and levels continuously, with its own risk overlays as backstop (docs 05, 06). It never assumes a thesis holds just because no new letter arrived. |
| **Entry lag** | Crown's "entered" positions were opened before we read the letter. We always start at a different, usually worse, price. | Crown's no-chase rule (≤3–5% from the published level) plus re-sizing from *our* fill. Stale entries are skipped and attributed as timing shortfall. |
| **Delivery time vs printed date** | Using the printed date in a backtest leaks future information if the letter actually arrived later. | Every letter carries a `received_at`. Backtests activate a letter only after `received_at` (doc 09). For historical letters where receipt time is unknown, assume next market open after the printed date and record the assumption. |
| **Image-only PDFs** | No text layer; extraction is vision-based and can misread numbers. | Arithmetic validation, page-referenced quotes, human spot-checks during paper soak, and a held-out set of hand-verified letters as a regression suite. |
| **Silence** | Crown sometimes stops mentioning a position without closing it. | Explicit "dropped" state with a configured policy (doc 06). |

## D. Data

| Constraint | Implication | Handling |
|---|---|---|
| **Alpaca free data feed** (IEX) — **VERIFY** plan | Quotes are from one venue; thin ETFs/options can look odd. | Use for monitoring and backtests; spread-aware limit orders; liquidity filters in doc 04. |
| **No reference data for futures/FX/index levels** on Alpaca | Crown's levels on SPX, HG, USDJPY can't be monitored directly. | Monitor via the closest tradable proxy with an explicit level translation (doc 04), or add a reference-data source. **Decision needed:** free sources (FRED, exchange delayed data) vs a paid feed. |
| **Historical options data** — **VERIFY** depth | Backtesting option expressions needs historical option prices. The v1 prototype assumed none. | If Alpaca's history covers the letter period, backtest options properly; otherwise options results are flagged approximate in every report. |

## E. Execution and costs

| Constraint | Implication | Handling |
|---|---|---|
| **Bid/ask spreads** | Small option orders can lose a large share of edge to the spread. | Max spread-as-%-of-premium filter; limit orders at mid with a bounded walk; skip if not filled. |
| **Agent-side stops depend on the agent** | If the VPS dies, a polled stop does nothing. | Broker-resident stop orders wherever possible (doc 05). |
| **Taxes** | All gains short-term; wash-sale rule on re-entry within 30 days of a loss. | Logged in the decision record; not optimized in v1. |

## F. Process

| Constraint | Implication | Handling |
|---|---|---|
| **Small sample** | ~17 letters in hand → tens of positions. Any tuned parameter will overfit. | Few parameters, coarse grids, walk-forward evaluation (doc 09). |
| **Hedged book, partial replication** | If we can't express a hedge leg, the remaining legs become a naked bet Crown never made. | Hedge-leg rule in doc 05: thesis-level sizing; unhedgeable legs are scaled down or skipped. |
| **Human availability** | Approvals arrive late or not at all. | Approval timeouts default to *no trade*; risk-reducing actions don't wait (doc 07). |

## Phase 0 verification list

- [ ] Account type confirmed cash; margin disabled.
- [ ] Options approval level granted; which structures it permits (long single-leg? debit spreads?).
- [ ] Fractionable flag for the likely universe (major ETFs, inverse ETFs, currency ETFs, Crown's single names).
- [ ] Order types available per instrument class (bracket / OTO / OCO; fractional restrictions).
- [ ] Market data plan (IEX vs SIP) and historical options data coverage for 2026-06 onward.
- [ ] Settlement handling and how Alpaca reports settled vs unsettled cash.
- [ ] Reference-data decision for futures/FX/index levels.
