# Paper-only research laboratory — 2026-10-21

## Mandate and current conclusion

Improve expected return after costs while preserving Crown's thesis and the cash-account
constraints. The owner explicitly prohibits real-money trading. `ALPACA_PAPER=true` is
mandatory; other values fail before credential access. Earlier live-launch instructions
are withdrawn. An optimization result cannot change account mode, permissions or limits.

The biggest bottleneck is fidelity and coverage, not insufficient parameter search.
The baseline June 1–October 16 replay has only two entries. Of 140 weekly position rows
available in that window, the protective compiler admits three rows, one of which still
does not fill. These are not independent bets. Source corrections and future letter
revisions can change these counts; the private manifest identifies the exact inputs.

Initial overlapping blocker counts: payoff/companion hedges 102, missing entry anchor 78,
conditional triggers 77, monitoring 61, source disagreement/audit 41, intentional Crown
restrictions 40, exit details 26. They cannot be added together. This is not evidence of
lost profit: unsupported trades have no defensible simulated P&L yet.

## Audit findings addressed

1. The environment helper accepted a live setting. It now rejects every value except
   `true` (missing defaults to paper); `.env` itself was neither opened nor edited.
   The only existing Alpaca TradingClient construction explicitly uses `paper=True`
   and belongs to the GET-only probe. No broker order adapter exists.
2. Some risk caps stayed at $5k defaults when capital changed. `research` configuration
   now specifies reserve, portfolio/thesis/cluster caps, weekly loss, costs and ATR values.
   Percentage caps scale with sleeve; separately stated dollar order/daily limits do not
   silently increase. `crown lab` requires exactly $5,000 per experiment.
3. Episode linking sorted by printed date before receiving evidence. It now follows
   availability and discards late older source updates without changing episode state.
4. Decision prose hardcoded 2/3 ATR even if another policy supplied different values.
   Both compilation and trailing-stop events now print the actual parameters.
5. Fill journals now explicitly record modeled fees for cost attribution.

No embedded two-trade P&L or ticker-specific result was found in the v3 execution path.
Tests perturb prices and future inputs, verify changed outcomes but unchanged earlier
decisions, exercise cash isolation and replay repeatability. Deterministic rules and
explicit assumptions are desirable; hardcoded successful outcomes are not. These tests
do not prove absence of every form of hindsight: the historical extraction model and
previously inspected corpus remain material limitations.

## Runnable experiments

```powershell
crown lab --from 2026-06-01 --to 2026-10-16
```

Always offline: no `.env`, LLM call, account access, OAuth or order submission. Existing
hash-validated letter and market-data caches are required. Every experiment has a new
$5,000 ledger, independent cash/settlement/reservations/loss caps, and SQLite hash chain.
Nine virtual budgets are comparisons, not nine Alpaca accounts, a pooled $45k book or
independent statistical observations. The code can simulate overlapping symbols in
separate ledgers; a future shared paper account needs allocation and fill reconciliation.

The fixed suite is strict source-only feasibility, protective control, half risk per RU,
1.5/3 ATR initial fallback, 2/4 ATR trailing distance, stressed costs and zero approval
latency. Each changes one main assumption relative to the control. A larger fallback
distance is chosen before entry and can never lower a printed stop or loosen an existing
stop. All source, shape, no-add and monitor gates remain enforced.

The initial suite produced losses in every variant that traded. Lower risk reduced the
dollar loss, which does not establish a better predictive edge. Trailing variants were
identical because the sample did not distinguish those choices. Exact results are private.

Artifacts are under ignored `data/reports/lab/<run>/`:

- `registration.json`: frozen recipe, policies, sources, bars, implementation and plan
  hashes written before the first replay. New runs never overwrite old runs.
- `study.json`, `index.html`: all comparisons, exposure/sample counts, source-linked
  Crown claims, supporting evidence, invalidations and an actionable blocker backlog.
- `replays/`: detailed equity curves and every decision, not only successful trades.
- Individual SQLite journals and `completion.json`: failed experiments retain their
  registration and failure type. Failure is not an empty successful backtest.
- `walkforward/`: 40-session initial training, expanding by 20 sessions, five-session
  embargo, and 20-session validation windows. Training inputs are sliced before replay.
  Selection is persisted before validation. Candidates require at least 20 closed
  episodes, positive modeled net and stressed P&L, and drawdown <=6%. These are coarse
  diagnostic rules, not proof of significance. No eligible candidate means cash.

Fold books reset to $5k and admit only newly available source updates; no fabricated
training positions carry across the boundary. Fold returns are not compounded or sold
as a continuous strategy. The corpus is already inspected: even chronological results
are exploratory algorithm checks, **not untouched out-of-sample evidence**. No selection
promotes itself to execution. A profitable research result still needs forward evidence.

## Learn Crown's reasoning without inventing it

The cached model already includes claim, drivers, evidence kind, horizon, invalidations,
conviction, structure, management instructions and source pages. The lab exposes these
as a dossier for each position observation. It does not treat a paraphrase as a verified
fact or claim to reproduce the desk's unpublished models.

Build and verify this chain for each actionable idea:

1. Crown claim and payoff: direction, relative versus absolute exposure, convexity,
   volatility exposure, horizon, catalyst, and required hedge legs.
2. Evidence: literal source claim versus measurable public observation versus proprietary
   or unavailable analysis. Do not substitute one for another silently.
3. Falsification: exact reference/units, threshold or event outcome, comparison convention,
   consecutive-session count, freshness and missing-data action. A missing threshold is
   not permission for the model to invent one.
4. Expression: scenario payoff across price, volatility and time; affordable granularity,
   fees, spread, carry, expiry and tracking error. Pair and short-volatility ideas cannot
   become unhedged directional ETF trades merely because those are cheaper.
5. Attribution: original feasible implementation versus the same inputs with one overlay.
   Attribute receipt lag, exclusions, sizing, substitution and costs separately.

The current private evidence counts include macro, technical, positioning, fundamental,
volatility and flow. Counts are per weekly position and include repeated theses. Useful
quantitative methods, in implementation order:

- Prior-only ATR/realized volatility and stop-distance risk sizing; already partly present.
- Release-aware COT net-position/open-interest percentiles and changes, aligned to the
  exact contract/category. `positioning_percentile` now provides a tested past-only
  primitive with tie handling and insufficient-history behavior. No CFTC downloader or
  automatic thesis mapping is connected yet.
- Measured curve/spread/rate changes with unrevised or vintage-correct observations;
  no full-sample z-scores or revised historical macro values masquerading as past facts.
- Conditional entry/exit state machines on exact reference instruments, then verified
  rolling correlation/beta and stress scenarios for candidate wrappers.
- Portfolio concentration, factor overlap and expected shortfall once representative
  history exists. Avoid a high-dimensional mean/variance optimizer on two trades.
- Options scenario/payoff analysis with verified contract multipliers and actual quotes;
  do not backfill historical executable options P&L from today's chain or Black–Scholes
  marks presented as observed fills.

These methods propose implementation research, not new directional alpha. Independent
signals, trade filters or direction changes remain clearly labeled Tier-3 shadow work.

## Free data and lawful access priorities

Use official interfaces and applicable access/retention terms first. Public visibility
does not by itself grant unrestricted scraping or redistribution. No login bypass,
paywall bypass, credential scraping or indiscriminate crawler is part of this plan.

| Source | Useful coverage | Limitation / next verification |
|---|---|---|
| Alpaca Basic | Existing IEX prices and news-access probe | Single venue; indicative options are not executable OPRA history. Capture actual feed/timestamps and cost assumptions. |
| Robinhood official MCP | Equity/index history and quotes, news, earnings, options research | Not connected in this session. Verify schemas, access, retention, refresh and unattended use. It can also trade real assets; this project permits only explicit research tools. |
| CFTC official public reports | Futures positioning and open interest | Tuesday measurement normally released Friday; holidays/delays matter. Exact contract/category alignment and original release evidence required. |
| FRED/ALFRED | Macro series and historical vintages | Revised final values must not enter old decisions; API key/terms and release timing need verification for selected series. |
| SEC EDGAR APIs | Filings, submissions and XBRL facts without an API key | Respect fair-access requirements; use filing acceptance time and amendments, not fiscal period end as availability. |
| Treasury/Fed/BLS/BEA official releases | Potential rates/policy/inflation/growth monitors | Select only series actually required by Crown; confirm timestamps, units, revision policy and allowed access before building adapters. |

Official references researched 2026-10-21:

- [Alpaca paper simulation limitations](https://docs.alpaca.markets/us/docs/paper-trading)
  and [data plans](https://docs.alpaca.markets/us/docs/about-market-data-api).
- [Robinhood official tool catalog and trading controls](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).
- [CFTC positioning and publication timing](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm)
  and [release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm).
- [ALFRED vintage data](https://alfred.stlouisfed.org/help/downloaddata).
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
- Bailey, Borwein, López de Prado and Zhu, [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf):
  repeated strategy selection requires accounting for the search, not reporting only the
  winning in-sample statistic. No significance/PBO/deflated-Sharpe estimate is claimed here.

`data/evidence.py` separates observation period, publication and actual local receipt;
requires exact units and provenance; excludes unavailable revisions; and returns
`unknown` for missing/stale data. `not_breached` only describes one condition, not overall
thesis health. These primitives are not yet wired into trading and do not relax current
monitoring skips. Historical downloads captured today are not contemporaneous receipts.

`data/robinhood.py` is a positive read-tool boundary with pinned catalog hashes. Unknown
tools, schema changes, account reads and mutations are rejected before dispatch. It is
not an OAuth implementation or a claim of a working connection. A future authorized
transport still needs timeout/rate-limit, schema validation, timestamp and retention checks.

## Next implementation priorities

1. Review actionable source fields and companion hedges, with targeted page checks rather
   than re-extracting the corpus. Distinguish genuine no-add restrictions from missing code.
2. Resolve validated conditional setups and the most common measurable invalidations.
   Add only the public-data adapters those monitors need; prove release/revision timing.
3. Add exact long-option/debit-payoff research and faithful ETF candidates where the
   source permits them. Missing historical quotes remain skips or explicitly separate
   payoff studies, never invented backtest fills.
4. Capture new letters immediately with `crown receive <pdf>`, then use cached/targeted
   extraction and review. The receipt records first local capture, not assumed delivery.
   Freeze prospective hypotheses before new weeks arrive; no test-period retuning.
5. After the earlier gates pass, implement a restartable paper worker with separate sleeve
   ownership, idempotent order intents, broker reconciliation, protection and watchdog.
   A common Alpaca paper account needs broker-level aggregate reservations plus internal
   per-sleeve allocation; current virtual accounts do not solve that deployment problem.

This release improves research integrity and makes constraints measurable. It does not
claim a profitable strategy, a full Crown replica, connected free feeds, or a running
autonomous paper-execution service. Recurring API spend was not increased: this study
uses existing caches only. Keep prospective LLM calls targeted within the existing cost
envelope rather than launching an unbounded self-optimization loop.
