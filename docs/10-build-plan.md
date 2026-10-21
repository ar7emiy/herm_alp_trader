# 10 — Build Plan

> 2026-10-21 owner mandate: real-money trading is prohibited. Paper only; see doc 16.
> Earlier references to later live deployment are superseded. Phase gates still apply
> to paper execution; research experiments do not waive them.

> 2026-10-19 completion update: [doc 13](13-completion-plan.md) refines these phases for
> autonomous paper and a private website; [doc 14](14-robinhood-and-data.md) covers provider
> research. Preserve phase order. Phase 4 requires deterministic operational scenarios and
> selective defensible replay, rather than an exhaustive cross-asset historical report.
> Paper permission policies are separate from live L0; no autonomy is enabled by this plan.

Build in phases; each has an exit criterion. Do not skip ahead to live trading. Backtest
and live share one engine, so most phases are built and proven offline first.

## Phase 0 - Inventory and verification

Replace assumptions with numbers before designing further.

Implementation progress, 2026-10-19: the paper-only GET probe now handles removed API
fields, records quote ages and news access, and keeps broker observations separate from
cash-only policy. Diagnostic errors omit provider messages that could echo a credential.
Offline tests explicitly prohibit credential-file loading. CI runs those tests and a
tracked-private-path check with the tested direct versions in `constraints.txt`.
Credentialed probe results stay under ignored `data/probe/`; capabilities remain
unverified until the remaining checks below are complete.

Three letters (June 1, June 22 and October 19) now have full visual page reviews and
source-corrected extraction records with 32 listed critical checks passing. v3.1 improves
PDF detail, price zones, ratio units, setup state and page coverage. The raw model still
misses some chart observations, risk-unit badges and scalar fields, and mislabels some
source pages/dates. Corrections are explicitly attributed to Codex visual review and
archived separately from provider output; they are not proof of autonomous accuracy.
`crown preview` makes these results inspectable in a private offline HTML report.
The older hand-built fixture is not a gold set. Cache reuse checks PDF content plus
prompt/glossary/schema/model settings. No audit/correction grants planning approval.
Next: automate the remaining critical checks against the reviewed corpus, then inventory
the other 17 letters in bounded batches; account/reference-data verification remains open.

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

## Subsequent phases — Paper research and bounded paper autonomy only

Real-money launch is withdrawn by the owner. Never switch to live keys or add a live
mode. Improve paper evidence, extraction, coverage, execution recovery and attribution;
paper category permissions remain owner-controlled.

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
- extract every position and lifecycle update on every page, not just the rows in the
  incomplete legacy fixture, with page references;
- flag the `●●●○` conviction as a glyph mismatch;
- classify the MU condor as `range_short_vol` + event-dependent → no directional
  substitute is eligible; skip (or a debit butterfly only if spreads are permitted);
- mark the MU entry window closed if run on/after Wednesday 6/24;
- never reactivate the old USDJPY trigger after the letter changes the setup;
- preserve GLD's reclaim zone without inventing a midpoint, close comparison or session count;
- keep quoted option points separate from contract dollars, using verified contract metadata;
- preserve conflicting chart/table observations and flag them instead of silently translating
  a SPY value into a supposedly printed SPX level;
- not veto anything for conflicting with the house view;
- produce a decision record for every one of the above, including skips.
