# Crown Letter Trading Agent

A self-hosted system that trades the ideas in **The Crown Macro Letter** — a weekly letter
from a macro hedge-fund desk — inside a small, cash-only account. Crown does the research
and picks the bets. This system's job is to **express each bet as faithfully as the
account's constraints allow, and to explain every place it had to deviate.**

> **This is not investment advice and not a guarantee of profit.** Automated trading can
> lose money quickly. You are responsible for every trade this system places. Build it in
> paper mode, measure it against Crown's own book, and move real money only once you trust
> it.

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

Phase 0 status: Alpaca paper and OpenAI read-only checks work; Robinhood is optional and
its OAuth connection can wait. Three letters now have page-reviewed, source-corrected
extractions with 32 passing critical checks. Raw model output still needs correction;
this is not unattended extraction accuracy or paper readiness. The remaining 17 letters,
reference-data decisions and later engine gates are outstanding. The old v1 compiler and
fixture remain superseded and are not validated trade inputs.

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
