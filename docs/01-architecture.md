# 01 — Architecture

## Design principles

1. **Crown is the signal; we are the implementation.** The system never invents a thesis
   or a direction. It decides *how* to express Crown's bets under constraints, and it
   records why.
2. **LLM for reading and proposing; code for deciding and enforcing.** The LLM turns
   unstructured inputs (image PDFs, video transcripts) into structured data and proposes
   candidates. Deterministic code validates, scores, sizes, enforces limits, and submits
   orders. Nothing about safety depends on the model behaving.
3. **One engine, two modes.** Backtest and live run the same planning, monitoring, and
   order-gateway code. Only four adapters differ: `Clock`, `MarketData`, `Approval`,
   `OrderSink`.
4. **Monitor in signal space, trade in execution space.** Crown's levels are stated on
   Crown's instrument (SPX, HG copper, USDJPY, MU). Thesis checks run against that
   reference instrument wherever data exists; orders are placed on whatever we actually
   hold (SPY, CPER, FXY, a long put). The translation between the two is explicit and
   logged.
5. **Everything is explainable after the fact.** Each decision produces a record linking
   Crown's words (with page reference) to the system's action and the constraint that
   forced any deviation.

## Components

```
┌──────────────────────────── CLOUD VPS (Ubuntu, systemd: crown-agent) ───────────────────────────┐
│                                                                                                  │
│  INGEST            UNDERSTAND                  DECIDE                         ACT                │
│  ───────           ──────────                  ──────                         ───                │
│  Gmail poller ──►  Extractor (LLM, vision) ──► Reconciler (code)                                 │
│  YouTube poller    Validator (code)            Substitution engine ─────────► Approval gate      │
│                                                 (LLM proposes, code scores)    (per autonomy lvl) │
│                                                Sizer + portfolio (code) ─────►      │             │
│                                                                                     ▼             │
│                    Monitors (code) ◄────────── Thesis/position state ──────► Order gateway       │
│                     price/level, regime,        (SQLite)                     (guardrails, code)   │
│                     events, vol, time                                               │             │
│                         │ escalate                                                  ▼             │
│                         ▼                                                     Alpaca (orders,     │
│                    Reviewer (LLM) ──► proposal ──► code decides                broker-side stops) │
│                                                                                                  │
│  Scheduler (code; computes cadence)   Notifier (Telegram: memos, approvals, alerts)             │
│  Decision log + audit store (SQLite)  Reports (weekly scorecard, attribution)                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
       ▲ SSH                     ▲ provider dashboard                 ▲ Alpaca dashboard
       └──────────── INDEPENDENT CONTROL PLANE (does not go through the agent) ────────────┘
```

### Ingest
- **Gmail poller** — detects new Crown letters (weekly and mid-cycle) and stores the PDF
  with its *received timestamp*. The received time, not the letter's printed date, is the
  point-in-time anchor for everything downstream (and for backtests).
- **YouTube poller** — RSS check for new Crown videos; pulls transcripts. Transcripts are
  treated as lower-authority updates (see doc 06).

### Understand
- **Extractor (LLM).** The letters are **image-only PDFs** (no text layer; 11–25 pages
  each), so extraction is a vision task. Output is the letter model in doc 03, with a
  page reference and a verbatim quote for every extracted claim. Cached per letter; a
  letter is extracted once unless the extractor version changes.
- **Validator (code).** Schema validation plus arithmetic and consistency checks (spread
  max-loss = width − credit, stops on the correct side of entry, dates in range, known
  tickers). Failures become review flags, never silent fixes.

### Decide
- **Reconciler (code).** Links each Crown position to its history across letters with a
  stable `crown_position_id`, and classifies it: new, unchanged, modified (stop/target
  moved), closed by Crown, or silently dropped. See doc 06.
- **Substitution engine.** The LLM proposes candidate expressions; code filters by hard
  feasibility and scores fidelity and cost with market data. See doc 04.
- **Sizer + portfolio (code).** Crown's risk-unit framework, portfolio-level caps, hedge-leg
  rules, and the exit plan. See doc 05.
- **Approval gate.** Applies the autonomy level for the action's category. See doc 07.

### Act
- **Order gateway (code).** The only path to the broker. Enforces every guardrail in
  doc 07 regardless of what upstream decided. Places entries with their protective stop
  attached at the broker whenever the instrument and order type allow it.
- **Monitors (code).** Cheap, deterministic checks on thesis invalidation conditions,
  Crown's levels, regime levels, the event calendar, volatility, and holding time. They
  call the LLM only on escalation.
- **Reviewer (LLM).** On escalation (a level breach that needs judgment, a new video, a
  mid-cycle letter), reads the relevant thesis, the trigger, and current data, and returns
  a structured proposal (hold / tighten / exit / no-op) with reasoning. Code applies it
  only if it is risk-reducing or passes the approval gate.

## LLM roles and contracts

| Role | Input | Output (structured) | Can it change risk? |
|------|-------|---------------------|--------------------|
| Extractor | Letter page images | Letter model (doc 03) | No |
| Substitution proposer | One Crown position + thesis + capability flags | Candidate list with rationale | No — code scores and chooses |
| Reviewer | Thesis, trigger event, market snapshot | hold / tighten / exit + reasoning | Only to reduce risk, via code |
| Memo writer | Decision record | Human-readable memo | No |

Every LLM output is schema-validated. A refusal, a schema failure, or a low-confidence
flag routes to human review; it never defaults to an action.

## State

All state lives in one SQLite database (plus cached artifacts on disk):

- `letters` — received time, file hash, extractor version, extraction JSON.
- `theses`, `crown_positions` — the letter model, linked across letters.
- `expressions` — chosen substitutions with scores and rejected alternatives.
- `positions`, `orders`, `fills` — our book (reconciled against Alpaca on every run).
- `monitors`, `events` — active watch conditions and their firing history.
- `decisions` — the decision record for every plan, approval, order, and exit (doc 08).

Alpaca is the source of truth for holdings and cash; the local positions table is a cache
reconciled on startup and before every order.

## What exists in code today

`src/crown/` contains a v1 prototype written against the previous spec: a letter
parser (Anthropic, trade-row schema), a plan compiler, an event-driven backtester, the
order gateway with guardrails, and tests. The **backtester, order gateway, market
calendar, bar fetching, and config loading carry over**; the parser schema and plan
compiler are replaced by docs 03–05. See doc 10 for the migration.
