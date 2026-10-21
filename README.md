# Crown Letter Trading Agent

A self-hosted system that trades the ideas in **The Crown Macro Letter** — a weekly letter
from a macro hedge-fund desk — inside a small, cash-only account. Crown does the research
and picks the bets. This system's job is to **express each bet as faithfully as the
account's constraints allow, and to explain every place it had to deviate.**

> **Paper only, by owner instruction (2026-10-21). Real-money trading is prohibited.**
> `ALPACA_PAPER` must be `true`; other values are rejected before Alpaca credentials are
> accessed. There is no live-mode switch or broker order adapter. Profitability is an
> empirical research objective, not an established property of this system.

---

## What the system is (and is not)

**It is a translation and portfolio-construction engine.** Each letter is read once into a
structured model of Crown's *theses* (what they believe, why, and what would prove them
wrong) and *positions* (how Crown chose to express each thesis). For every Crown position
the system finds the best expression available to a $5k cash account — the same
instrument, an ETF proxy, an inverse ETF, a long option, or a deliberate skip — sizes it
with Crown's own risk framework, watches the thesis between letters, and exits when the
thesis or the position's risk says so.

**It is not a second research desk.** It does not re-derive Crown's positioning, flow, or
macro analysis. Crown's analysis is the signal. The system's own judgment is confined to
*implementation*: instrument choice, sizing granularity, timing, and risk overlays — each
one tracked and attributed so you can see whether it helped or hurt (see
[docs/09-evaluation.md](docs/09-evaluation.md)).

## Documents

Read in order. These are the build spec.

| Doc | Purpose |
|-----|---------|
| `README.md` | Orientation and decision log |
| [docs/01-architecture.md](docs/01-architecture.md) | Components, data flow, where the LLM is used and where it is not |
| [docs/02-constraints.md](docs/02-constraints.md) | Every constraint that prevents 1:1 replication, and how each is handled |
| [docs/03-letter-model.md](docs/03-letter-model.md) | Thesis-first extraction schema, parsing rules, cross-letter linking |
| [docs/04-substitution-engine.md](docs/04-substitution-engine.md) | Turning a Crown position into the best feasible expression |
| [docs/05-portfolio-and-risk.md](docs/05-portfolio-and-risk.md) | Sizing, portfolio caps, the exit stack, deviation tiers |
| [docs/06-monitoring-and-lifecycle.md](docs/06-monitoring-and-lifecycle.md) | Thesis tracking between letters, letter reconciliation, scheduling |
| [docs/07-safety-and-autonomy.md](docs/07-safety-and-autonomy.md) | Kill switches, code-enforced guardrails, graduated autonomy |
| [docs/08-explainability.md](docs/08-explainability.md) | Decision records, memos, the audit trail |
| [docs/09-evaluation.md](docs/09-evaluation.md) | Crown-replica benchmark, shortfall attribution, backtest rules |
| [docs/10-build-plan.md](docs/10-build-plan.md) | Phased build with exit criteria |
| [docs/11-runbook.md](docs/11-runbook.md) | Day-to-day operation and failure modes |
| [docs/12-glossary.md](docs/12-glossary.md) | Crown's vocabulary and operating principles (from the base guide) |

## Current completion plan (2026-10-19)

**2026-10-21 research update:** [doc 16](docs/16-research-laboratory.md) records the system
audit, quantitative research process, free-data priorities and current implementation.
`crown lab` runs nine independent $5,000 simulations, records every hypothesis before
execution, compares costs/risk/timing, and builds source-linked Crown evidence dossiers.
It includes chronological training/validation with an explicit insufficient-evidence
outcome. Previously examined history is never relabeled as an untouched holdout.

```powershell
crown lab --from 2026-06-01 --to 2026-10-16  # always offline; no credentials
crown receive path/to/new-letter.pdf       # capture first local receipt now, never backdate
```

Multiple budgets are virtual independent accounts, not Alpaca subaccounts. Every account
has its own cash, reservations, loss limits and journal; broker execution remains disabled.
The lab does not remove source/fidelity gates to manufacture trades. Free-data evidence
primitives and a Robinhood read-tool boundary are tested foundations, not connected feeds.

The baseline is committed; the trading service and website are still to be built.
[docs/13-completion-plan.md](docs/13-completion-plan.md) defines the path to autonomous
paper trading and a private operations dashboard, including release gates and the smaller
first instrument scope. [docs/14-robinhood-and-data.md](docs/14-robinhood-and-data.md)
records official Robinhood MCP research and current Alpaca assumptions to verify.

These updates preserve Phase 0 first. Historical replay remains useful, but an exhaustive
cross-asset backtest is not a prerequisite for paper; deterministic operational scenarios
and forward paper evidence are required. No paper or live orders are enabled by these docs.

Crown letters/source PDFs, credentials, and runtime data stay local and are gitignored.
The project requires separately supplied private letters; none are distributed with it.

**Historical research replay is now usable; paper execution remains disabled.** All 20
letters have cached v3 extractions and independent vision fact readings: 144 position rows
and 112 thesis rows, not unique positions. Three letters retain full-page source reviews
and 32 passing critical checks. Automated audits still report 39 structural errors across
the corpus; only 33 of 52 plain-long rows agree on the independently read execution fields.
Agreement is not accuracy: source review caught a no-add restriction missed by both reads.
Alpaca/OpenAI application checks work; Robinhood remains optional and unconnected.

The default `crown backtest` now uses the v3 research engine: settled-cash accounting,
pending-order reservations, sizing/caps, stop ratchets, lifecycle/evidence timeouts, prior
ATR, conservative daily fills and an append-only decision journal. It supports identical
plain long stocks/ETFs only; unimplemented payoff shapes, hedges and required references
are skips. The ATR/fractional comparison is explicitly unapproved shadow research.

```powershell
crown prepare --offline       # refresh source audits using completed caches; no API calls
crown inventory
crown backtest --from 2026-06-01 --to 2026-10-16 --offline
```

Open the private `index.html` path printed by the backtest. Each run also writes JSON,
input/code hashes and SQLite decision journals under ignored `data/reports/replay/`.
An offline rerun loads no credentials and makes no API calls. Omit `--offline` only to
fetch missing historical IEX bars through the application's credential loading.
The superseded Anthropic commands require explicit `legacy-compile` / `legacy-backtest`.
See [docs/15-research-replay.md](docs/15-research-replay.md) for scope, assumptions, known
limits and remaining paper gates. This is not a full Crown replica or a profitable strategy.

Run `crown preview` to generate `data/reports/phase0-results.html`, a private offline
report of real extracted theses, account constraints, risk budgets, source corrections
and unresolved exits. Open the file locally; it is not a deployed trading website.
`crown audit <pdf>` records preliminary checks and private page findings; it never approves
a plan. Neither command loads credentials or calls a provider. Source corrections are
recorded with `crown correct`; original attempts remain archived. None of these reports
or private inputs belong in git.

## Decision log

| # | Decision | Status |
|---|----------|--------|
| D1 | **Broker: Alpaca**, paper first. | Decided |
| D2 | **Account type: cash.** No margin, no short selling, T+1 settlement. Bearish views are expressed by *owning* something (inverse ETF, long put, or debit put spread if the account's options level allows it). | Decided 2026-10-02 |
| D3 | **Sleeve: $5,000 IS the tactical sleeve** (investable assets held elsewhere). A 1.00 Crown position risks 1.5% of the sleeve = **$75**. Set in `config.yaml`; never inferred. | Decided |
| D4 | **LLM provider: OpenAI API.** Used for letter extraction (the letters are image-only PDFs, so this is a vision task), candidate generation for substitutions, escalation reviews, and memo writing. Never for order submission or risk limits. | Decided 2026-10-02 |
| D5 | **Thesis-first design.** Crown's theses are first-class objects; trades hang off theses. Monitoring, exits, and explanations are all keyed to the thesis, not only to price levels. | Decided 2026-10-02 |
| D6 | **Deviation tiers** (replicate → forced deviations → risk overlays → own alpha). Tiers 0–2 may trade; tier 3 runs in shadow paper only. Every deviation is attributed in evaluation. | Decided 2026-10-02 |
| D7 | **Graduated autonomy** replaces "approve every order forever." Risk-*reducing* actions from an approved plan (stops, exits, tightening) execute without a fresh approval at every level; risk-*adding* actions earn autonomy per category after a measured track record. Kill switches and code guardrails never relax. See doc 07. | Decided 2026-10-02 |
| D8 | **Orchestration: our own Python service** (systemd) with a deterministic scheduler, replacing the earlier Hermes-agent plan. The backtest and live system run the same engine; only the clock, data, approval and order adapters differ. The LLM never edits the schedule — cadence is computed by code. | Proposed — confirm |
| D9 | **Where it runs: cloud VPS**, not a home machine, for an independent control plane (provider dashboard hard-stop, SSH from anywhere). | Decided |
| D10 | **Prediction markets: out of scope for v1.** Alpaca does not offer them; a second venue (e.g. Kalshi) adds a second failure surface. Revisit after paper soak. | Decided |
| D11 | **Paper only, permanently within the current mandate.** No real-money orders, no environment toggle to live. Earlier live-launch phases are withdrawn. | Decided 2026-10-21 |

## High-level flow

```
 Letter arrives (Gmail; weekly + occasional mid-cycle)
   │
   ▼
 EXTRACT  (LLM, vision)  ──► Letter model: theses, Crown positions, levels, events,
   │                         lifecycle updates — every field with a page reference
   ▼
 VALIDATE (code) ──► schema + arithmetic checks; anything inconsistent → human review
   │
   ▼
 RECONCILE (code) ──► link to prior letters: new / still open / modified / closed / dropped
   │
   ▼
 SUBSTITUTE (LLM proposes, code scores) ──► best feasible expression per Crown position
   │
   ▼
 SIZE + PORTFOLIO (code) ──► Crown risk units → sleeve dollars, caps, hedge-leg rules
   │
   ▼
 APPROVE (per autonomy level) ──► EXECUTE (broker-resident stops) ──► LOG decision record
   │
   ▼
 MONITOR between letters (code; LLM only on escalation) ──► exit / tighten / alert
```

## Cost summary (target)

| Item | Cost |
|------|------|
| Cloud VPS (2 GB) | ~$6–12/mo |
| OpenAI API (one vision extraction per letter + sparse escalations) | ~$3–10/mo |
| Alpaca paper + live, basic market data | Free |
| Reference data for non-Alpaca instruments (futures/FX/index levels) | $0 if free sources suffice; decision in doc 02 |
| **Total** | **~$10–25/mo** |

If actual usage drifts far from this, flag it.
