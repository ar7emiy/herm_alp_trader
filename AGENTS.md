# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this project is

A self-hosted system that trades the ideas in **The Crown Macro Letter** (a weekly macro
hedge-fund letter) inside a **$5,000 cash account** at Alpaca. Crown supplies the theses
and bets; the system finds the most faithful expression of each bet that the account can
hold (same instrument, ETF proxy, inverse ETF, long option, or skip), sizes it with
Crown's risk-unit framework, monitors the thesis between letters, exits when the thesis
or the risk says so, and explains every deviation from Crown.

## The spec

`README.md` (decision log) and `docs/01`–`docs/12` are the build spec, rewritten
2026-10-02 (v3, thesis-first). Read them in order. **If asked to build, follow the phases
in `docs/10-build-plan.md` in order — Phase 0 (letter inventory + account verification)
comes first; don't skip to live trading or live adapters.**

## Non-negotiables

- **Crown is the signal.** The system never invents a thesis or a direction. Its own
  judgment is limited to implementation (instrument, size granularity, timing, risk
  overlays), and every deviation is tagged with a tier (doc 05) and attributed in
  evaluation (doc 09). Tier-3 "own alpha" ideas run in shadow paper only.
- **Cash account.** No margin, no shorting, no credit spreads/naked options, buy only
  with settled cash (T+1). Bearish = own something (inverse ETF, long put, debit spread
  if the options level allows).
- **LLM proposes; code decides and enforces.** The LLM (OpenAI API) extracts letters
  (image-only PDFs → vision), proposes substitution candidates, reviews escalations, and
  writes memos. It never submits orders, never sets limits, and its outputs are
  schema-validated. All guardrails live in the order gateway (doc 07).
- **Never assume a thesis holds between letters.** Every open position always has an
  exit plan (doc 05); new letters are reconciled against the book (doc 06).
- **Stops only tighten.** Code rejects any loosening, from any source.
- **Graduated autonomy.** Risk-reducing actions from an approved plan execute
  automatically; risk-adding actions need approval until a category earns autonomy by
  the doc 07 criteria. The system cannot raise its own autonomy level. Kill switches
  (Alpaca key revoke → VPS power-off/SSH → Telegram) and guardrails never relax.
- **Explainability is a precondition.** A decision that can't produce its decision record
  (doc 08) doesn't execute.
- **Cloud VPS, not home hardware** — for an independent control plane. Don't re-litigate
  without the user.

## Current state of the code

`src/crown/` is a **v1 prototype written against the previous (superseded) spec**: an
Anthropic-based parser with a trade-row schema, a plan compiler, an event-driven
backtester, the `OrderGateway` guardrails, and tests (52 passing as of 2026-10-02).
`docs/10-build-plan.md` → "Migrating the v1 prototype" says what is kept (backtest event
loop, order gateway, calendar, bar fetching, config loading) and what is replaced
(parser → OpenAI vision extractor on the doc 03 schema; `models.py`; `build_plan.py` →
substitution + sizing). The v1 parser still imports the Anthropic SDK (installed via the
`legacy` extra) until Phase 1 removes it.

Phase 0 tooling (spec v3) is in place: `src/crown/letter/` (thesis-first schema,
OpenAI vision extractor cached per `llm.extractor_version`, inventory report) and
`src/crown/probe.py` (read-only Alpaca capability probe — GET calls only).

The letters in `crown_letters/` are **image-only PDFs** (no text layer); the base guide
PDF has a text layer.

## Environment

```
python -m venv .venv
.venv/Scripts/activate        # Windows; source .venv/bin/activate on Linux/macOS
pip install -e ".[dev,legacy]"
cp .env.example .env          # OPENAI_API_KEY, Alpaca PAPER keys, ALPACA_PAPER=true
pytest
```

Phase 0 commands:

```
crown llm-check               # list OpenAI models for the key; ping llm.extract_model
crown extract <pdf> | --all   # vision-extract letters (cached; one API call per uncached letter)
crown inventory               # data/reports/phase0-inventory.md from cached extractions
crown probe                   # read-only Alpaca account/capability probe → data/probe/
```

Backtest and live share one engine; only the `Clock` / `MarketData` / `Approval` /
`OrderSink` adapters differ. Live adapters (Alpaca orders, Gmail, Telegram) are Phase 5 —
don't build them earlier.

Expected run cost ~$10–25/mo (VPS + sparse OpenAI calls). Flag it if an implementation
drifts far from that.
