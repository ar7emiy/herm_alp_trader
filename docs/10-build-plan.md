# 10 — Build Plan

Build in phases; each has an exit criterion. Do not skip ahead to live trading. Backtest
and live share one engine, so most phases are built and proven offline first.

## Phase 0 — Inventory and verification

Replace assumptions with numbers before designing further.

- [ ] **Letter inventory.** Extract all letters in `crown_letters/` (OpenAI vision, doc 03
      schema, first pass may be rough) and produce a table:
      positions per letter; by asset class and structure; how many need substitution;
      how many are hedge legs / pairs; how often Crown states a stop, targets, an explicit
      invalidation, a sizing multiplier; how often later letters close or modify a position;
      how often positions are dropped silently.
- [ ] Note gaps in the letter series (e.g. no issue dated 8/31 — confirm whether one exists).
- [ ] **Account probe** (doc 02 verification list): cash/margin status, options level,
      fractionable flags for the likely universe, available order types, data plan,
      historical options coverage, settled-cash reporting.
- [ ] **Reference-data decision** for futures/FX/index levels.
- [ ] Hand-verify **3 letters** field by field (include 6/22) → the extraction gold set.

**Exit:** inventory report reviewed with you; `capabilities` block in config filled from
the probe; open decisions in the README resolved.

## Phase 1 — Letter model

- [ ] Pydantic models for doc 03 (letter, thesis, crown_position, lifecycle_update, levels,
      events, house_view, review_flags) with `source` provenance.
- [ ] Extractor on OpenAI (vision input of page images; structured output), cached per
      letter and versioned.
- [ ] Validator (arithmetic, sides, dates, tickers, references).
- [ ] Linker (stable `crown_position_id` / `thesis_id` across letters).

**Exit:** gold-set accuracy ≥ 98% of fields, zero errors in levels/strikes/multipliers;
every validator rule has a unit test; the full letter series links into consistent
position histories.

## Phase 2 — Substitution, sizing, portfolio

- [ ] Exposure-profile builder; candidate generation (LLM) + hard filters + scoring (code).
- [ ] Level translation (identity, ratio, inverse, reciprocal, beta) with confidence.
- [ ] Sizing at fill price; rounding rules; caps; hedge-leg rule.
- [ ] Expression records and the decision record (doc 08).

**Exit:** for every Crown position in the series, an expression record (including skips)
that you have reviewed for the first few letters and agree with; unit tests for each
worked example in doc 04.

## Phase 3 — Lifecycle, monitors, exits

- [ ] Thesis and position state machines (doc 06).
- [ ] Monitors: entry, stop/target, invalidation, regime, event, vol, time, overlay stops.
- [ ] Exit stack with ratchet (doc 05).
- [ ] Letter reconciliation (new / unchanged / moved / resized / closed / dropped).
- [ ] Reviewer LLM role with a deterministic stand-in for backtests.

**Exit:** scenario tests for each reconciliation case and each exit; no open position in a
full-series replay ever lacks an exit plan.

## Phase 4 — Evaluation harness

- [ ] Crown replica, feasible replica, and actual books (doc 09).
- [ ] Shortfall attribution and fidelity metrics.
- [ ] Walk-forward split; overlay on/off comparisons.
- [ ] Reports: weekly scorecard, deviation log, skip log.

**Exit:** a full-series report you've read, with overlays individually justified or
switched off.

## Phase 5 — Infrastructure and live adapters (paper)

- [ ] VPS: Ubuntu, SSH keys only, firewall, non-root user, `crown-agent` systemd unit;
      power-off from the provider app verified.
- [ ] Secrets in `.env` (`chmod 600`, never committed): `OPENAI_API_KEY`, Alpaca **paper**
      keys, Telegram bot token + chat id, Gmail access.
- [ ] Adapters: Gmail ingest (with `received_at`), Alpaca market data, Alpaca paper
      orders (bracket/OTO where possible), Telegram memos + approvals, YouTube poller.
- [ ] Scheduler (doc 06), watchdog with read-only keys (doc 07), HALT handling.

**Exit:** one live week end-to-end in paper: letter ingested, plans produced, approvals
round-tripped, monitors running, all logged.

## Phase 6 — Paper soak (4–8 weeks)

- [ ] Run on real incoming letters at autonomy L0.
- [ ] Weekly: extraction audit, review of every decision record, scorecard + attribution.
- [ ] Test every kill-switch layer and every pre-live checklist item (doc 07).

**Exit:** doc 09 paper-soak criteria met.

## Phase 7 — Live at L0

- [ ] Fund the account with an amount you can afford to lose.
- [ ] Switch to live keys. Change nothing else that day.
- [ ] Every entry approved; risk-reducing actions automatic (D7).

## Phase 8 — Graduated autonomy

- [ ] Promote categories one level at a time per doc 07 criteria; demotion automatic.

## Migrating the v1 prototype (`src/crown/`)

| v1 module | Fate |
|---|---|
| `engine/orders.py` (OrderGateway, guardrails) | **Keep**, extend: settled cash, no-shorts, options capability, open-risk caps, ratchet, weekly loss |
| `backtest/runner.py` (event loop) | **Keep** the event loop; rewire to theses/expressions/exit stack |
| `engine/zonecheck.py`, `engine/fills.py` | Keep, extend (cadence tiers, options fills) |
| `data/alpaca_bars.py`, `data/calendar.py`, `config.py` | Keep |
| `compiler/parse_letter.py` | **Replace** — OpenAI vision extractor, doc 03 schema |
| `models.py` | **Replace** with doc 03 + execution models |
| `compiler/build_plan.py` | **Replace** by substitution + sizing modules (docs 04–05) |
| `tests/test_micron_regression.py` | Rewrite against new expectations (below) |

### June 22 regression (updated expectations)

A correct run on the 6/22 issue must:
- extract the MU condor, GLD put spread, MSFT bear call, the two pairs, and the USDJPY
  conditional into theses + positions with page references;
- flag the `●●●○` conviction as a glyph mismatch;
- classify the MU condor as `range_short_vol` + event-dependent → no directional
  substitute is eligible; skip (or a debit butterfly only if spreads are permitted);
- mark the MU entry window closed if run on/after Wednesday 6/24;
- never stage the USDJPY short unless 157.50 prints in its window; if it does, the
  candidate is long FXY with a reciprocal level translation;
- record GLD's invalidation as a price condition (close above ~391.5) on the thesis;
- not veto anything for conflicting with the house view;
- produce a decision record for every one of the above, including skips.
