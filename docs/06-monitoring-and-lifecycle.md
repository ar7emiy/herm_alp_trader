# 06 — Monitoring and Lifecycle (between letters)

The core rule: **a thesis is never assumed to hold just because no new letter arrived.**
Between letters the system watches Crown's own invalidation conditions and levels, applies
its risk overlays, and reconciles every new letter or update against what it holds.

## Lifecycle states

```
Crown position:   watching ──trigger──► eligible ──approved/filled──► open ──► closed
                     │                     │                            │
                     └──window closes──────┴──no-chase / skip──► expired/skipped

Thesis:           active ──► weakened ──► invalidated
                    ▲            │
                    └─ restored ─┘ (only by a new Crown letter/update)
```

- **active** — no invalidation condition has fired.
- **weakened** — a soft signal fired: regime level crossed against the thesis, an
  event outcome went the wrong way but no explicit invalidation printed, or a reviewer
  escalation judged it at risk. Effect: no new entries on this thesis; open positions get
  their stops tightened (tier 2) — not closed.
- **invalidated** — an explicit Crown invalidation condition fired, or Crown closed it.
  Effect: all positions on the thesis exit (tier 0).

Only a new Crown letter or update can move a thesis back from weakened to active. The
system never "un-invalidates" a thesis on its own.

## Monitors

All monitors are deterministic code. Each one, when it fires, produces an event row and
(if configured) an escalation.

| Monitor | Watches | Fires on | Default action |
|---|---|---|---|
| Entry | conditional triggers, entry bands, window close | trigger prints in window / window closes | stage entry / expire |
| Stop & target | open positions (reference + execution space) | level crossed (with session counts for behavioral rules) | exit / trim |
| Thesis invalidation | each thesis's `invalidation` list | condition met | invalidate thesis → exit |
| Regime | zero-gamma, put wall, call wall from `levels` | close crosses level against an open thesis | weaken thesis → tighten stops |
| Event | `events` calendar | T−1 day before an event affecting an open position; event passed | de-risk per exit plan; post-event review |
| Volatility | ATR / realized vol per held instrument | vol regime jump (e.g. ATR > 2× entry ATR) | escalate |
| Time | days held vs exit plan | time stop reached | exit |
| Overlay stops | trailing / vol stops | level crossed | exit (broker-side when possible) |

## Escalation to the LLM reviewer

Only these escalate to an LLM call:

- A narrative-type invalidation condition might have been met (needs judgment).
- A regime crossing or vol jump against an open thesis.
- A new mid-cycle letter or YouTube video touching an open thesis.
- A validator or linker flag that blocks a plan.

The reviewer returns `hold | tighten | exit | flag_human` with reasoning tied to the
thesis. Code applies `tighten` and `exit` immediately (risk-reducing). Nothing the reviewer
returns can open or enlarge a position.

## Reconciling a new letter

On every new letter (weekly or mid-cycle), after linking (doc 03), each open or watched
Crown position falls into exactly one case:

| Case | Detected by | Action |
|---|---|---|
| **New** | no matching history | substitution → sizing → approval |
| **Unchanged** | listed with same terms | keep; refresh levels and translations |
| **Stop/target moved** | `lifecycle_updates` or changed terms | apply if tighter; flag if wider (never auto-loosen) |
| **Resized** | multiplier changed | reduce automatically; increases go through approval |
| **Closed by Crown** | `closed` update or Closed Trades section | exit at next opportunity (tier 0) |
| **Dropped silently** | was open, not mentioned at all | `dropped_policy` (config): default **tighten to trailing stop and set a 5-day time stop**; alternative `exit` |
| **Thesis restated** | thesis text changed | re-run invalidation extraction; diff logged |

The reconciler also compares our book against Crown's: any position we hold that Crown no
longer holds (or vice versa) appears in the weekly report.

## Mid-cycle letters and videos

- **Mid-cycle letters** go through the same pipeline as weekly ones.
- **YouTube transcripts** are lower authority: they can *weaken* a thesis or trigger a
  review, and a clear Crown statement ("we're out of the gold puts") is treated as a
  lifecycle update — but they never create new positions on their own. Transcript
  extraction is logged with timestamps so the claim can be checked.

## Scheduling

The scheduler is code (D8), not the LLM. Cadence per symbol is computed from distance to
the nearest live level:

| Distance to nearest entry / stop / invalidation level | Cadence |
|---|---|
| > 5% | hourly (market hours) |
| 2–5% | every 30 min |
| < 2% or inside a band | every 10–15 min |
| No live levels | end of day only |

Fixed jobs:

| Job | When |
|---|---|
| Gmail letter check | every 30 min, any day |
| Monday anchor (confirm the weekly letter was processed) | Monday 06:00 ET |
| End-of-day pass (behavioral session counts, regime closes, time stops, cluster refresh) | 16:05 ET |
| Pre-event pass | 08:00 ET on each event day from the calendar |
| YouTube check | daily 18:00 ET |
| Account reconciliation with Alpaca | on startup and before every order |
| Independent watchdog (OS cron, separate process) | every 5 min — see doc 07 |

All times are stored and computed in `America/New_York`; the host clock is UTC.
Tight cadences are capped (`max_tight_symbols`) and expire automatically when the level is
resolved; no job can outlive its reason.
