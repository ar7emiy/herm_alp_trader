# Historical research replay (2026-10-19)

The user's current completion target permits a viable backtest as an alternative to
paper execution. This release provides a reproducible, selective historical research
replay. It does not pass the outstanding extraction, fidelity, infrastructure or paper
acceptance gates in docs 10 and 13. No broker order adapter or running trading service
exists. Robinhood is optional and remains unconnected.

## Inputs and evidence

All 20 local letters have cached v3.1 extraction and independent vision fact readings.
Three corrected letters retain full-page reviews and 32 critical checks. The inventory
contains 144 position rows and 112 thesis rows; these are repeated weekly observations.
There is a missing weekly issue between August 24 and September 7. No August 31 receipt
or source is available, and no thesis continuity is assumed through that gap.

The corpus still has 39 automated structural audit errors. Independent reads agree on
only 33 of 52 plain-long execution rows, including watch-only/non-executable rows. Two
calls to the same model are not independent evidence of accuracy. Visual review found
shared source-page mistakes and a shared omission of an explicit no-add restriction.
The restriction was corrected with provenance and blocks new exposure; original provider
attempts remain archived. Unattended extraction and 98% field accuracy are unproven.

The recorded 41 completed extraction/fact-reading attempts used 796,243 input and 534,707
output tokens, an estimated $6.26 at the previously verified GPT-5 rates. This is a one-time
preparation cost, not the recurring monthly cost or an invoice; tiny access checks and
failed requests without reported usage are excluded. Replay itself is offline.

## Run it

Install the project using the README environment instructions, supply private letters
under `crown_letters/`, and prepare missing reads with `crown prepare --workers 4`.
That command can call OpenAI and loads credentials inside the application only; completed
matching caches are reused. Do not force the corpus through the model again.

With the existing caches:

```powershell
crown prepare --offline
crown inventory
crown preview
crown backtest --from 2026-06-01 --to 2026-10-16 --offline
```

`--policy strict`, `--policy protective_research`, or the default `both` selects fixed
policies. `--out data/reports/replay/my-new-run` chooses a new private output folder.
Existing output is never overwritten; output outside the private reports directory is
rejected. Omit `--offline` to GET missing historical IEX bars using application-loaded
credentials. Backtesting never extracts letters or invokes an LLM. Missing source reads
fail closed; missing bar caches in offline mode cause an explicit error.

The private HTML report has policy selection, an equity plot and searchable decisions.
`results.json`, `manifest.json` and one SQLite journal per policy are alongside it. The
manifest binds PDFs, exact corrected extractions, independent reads, policies, compiled
plans, implementation files and market-data hashes. A run is one-shot, not a resumable
daemon. All artifacts contain private source material and stay under ignored `data/`.

## Research boundaries

- Only identical plain long stocks/ETFs execute. Options, pairs, futures, FX, crypto,
  index references and unsupported payoff shapes get skips. An unsupported linked hedge
  or compound thesis blocks the core. An affordable ETF is not automatically an equivalent
  substitute for an option payoff.
- Unique exact instrument/structure episodes link across letters. A stopped-out episode
  cannot re-enter on the next reminder. Re-entry needs a corroborated explicit closure
  and a later reopened episode. Closing an option overlay cannot close a stock core.
  Semantic cross-instrument linking is unverified, so all exposures share one conservative
  thesis and risk cluster for caps.
- Discrepant facts, conditional/event triggers, missing quotes, relevant structural errors,
  unmonitorable invalidations and no-add restrictions block entries. Targets need
  corroborated scalar values and supported actions. Required references are not replaced
  with an unlogged inference. No own directional alpha is invented.
- A missing invalidation list does not prove a thesis is healthy. Price protection and
  evidence timeouts cannot reconstruct a macro desk's monitoring or detect every
  fundamental thesis break. The report labels these limitations.
- Unknown receipts use an explicit assumption: next NYSE open strictly after the printed
  date, then configured approval latency (45 minutes). Daily execution waits for the next
  eligible open. Actual receipt precision is not recovered. Late older letters cannot
  overwrite newer evidence. Only completed sessions replay.
- Strict requires a published entry and usable protective observation rule. Shadow
  research allows joining existing holdings without an anchor, uses 0.001-share increments
  and prior-only 14-session mean true range. Protection is the higher of the printed
  numerical stop and entry minus 2 ATR; an ambiguous convention becomes touch only in
  this experiment. A 3 ATR trail activates after a close at +1 initial R and applies the
  next session. Stops only tighten.
- Unstated risk units use an explicit 0.25 RU fallback, never conviction. Research defaults:
  $5,000 sleeve, $75/RU, 10% reserve, $2,000/order, four positions, $300 total open risk,
  $112.50/thesis, $150/cluster, and $150 daily/$300 rolling-five-session loss limits.
  These defaults do not grant paper permissions.
- Decimal cash/risk is reserved before fills, including fees and outstanding intents.
  Partial/duplicate fills cannot overspend. Sales remain unsettled until the next NYSE
  session that is also a federal banking business day. This conservative funding calendar
  is not verified broker settlement reporting.
- Daily fills model 5 bps slippage and 1 bp rounded-up fees per side. Gap-through stops
  use the worse open; stops precede same-bar targets. Close-based stops require their
  stated count and fill next open. Targets use fixed base-guide trim/exit rules, with a
  logged full exit if a half trim rounds to zero.
- Evidence expires after five sessions. Omission/data failure causes a conservative risk
  exit, not an assertion that Crown sold. Missing bars keep exits pending and disclose
  unmodeled gap risk. HALT blocks new exposure while reductions continue.
- Every intent/fill has sources, hashes, a reason and deviations. SQLite decisions are
  append-only and hash chained. Failure to record prevents the fill.

## Interpretation

The comparison covers June 1 through October 16, 2026, using 20 frozen source letters;
19 become available within that window. Strict stays in cash. Protective research enters
only two setups. This is useful operational evidence, not enough trades to assess
expected return, select ATR parameters or claim an optimum. Exact figures/fills live in
the private report and are not distributed with the code.

IEX is a single-venue raw daily feed, not observed consolidated executable quotes. Daily
intrabar ordering, spreads and impact are unknown. Dividends/distributions and verified
corporate actions are not modeled; a raw open/previous-close discontinuity above 40%
aborts rather than reporting a split loss, but smaller actions can remain. P&L is
price-only. Open positions are marked, not silently liquidated at the end.

Historical model reads can share errors and hindsight; receipts are assumed. The strict
cash book is not Crown's replica. The policy comparison is not causal overlay attribution.
There is no historical options reconstruction or validated return benchmark. A positive
or negative result does not establish the value of Crown's ideas.

## Paper gates still due

1. Audit actionable fields and management restrictions; reconcile full histories and
   compound bets. Translate narrative invalidations into approved measurable evidence
   with actual feed coverage, freshness checks and missing-data behavior.
2. Resolve substitutions/hedges using scenario/payoff fidelity, actual asset validation,
   granularity and execution costs. Validate semantic thesis/cluster linking.
3. Verify legal cash status, settlement/activity reporting, account permissions, protective
   order acceptance, fractions and rejected/partial/canceled order flows. Paper buying
   power or fractionable flags cannot certify these facts.
4. Build restart/reconciliation, authenticated owner policy/approvals, scheduler, watchdog,
   independent kill switches and VPS deployment. Then add the paper-only broker adapter,
   complete operational acceptance and run the forward paper soak.

Never attach this daily simulator directly to broker mutations or relax fidelity gates
merely to increase the trade count.
