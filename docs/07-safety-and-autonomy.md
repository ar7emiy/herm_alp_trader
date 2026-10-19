# 07 — Safety and Autonomy

> 2026-10-19 paper-only clarification: doc 13 proposes autonomous paper entries within
> an explicit owner-set category policy after Phases 0-5 validation. Live remains L0 until
> separately authorized/promoted; paper autonomy cannot promote live. HALT must preserve
> genuine risk reductions, evaluated at package/portfolio level. Do not assume a distinct
> read-only Alpaca retail key exists; use verified scopes or an enforced GET-only interface.
> Runtime defaults have not changed.

Guiding principle: **you must be able to stop trading without the agent's cooperation,
and nothing about safety depends on the LLM behaving.** Autonomy is earned per category
of action; the kill switches and code guardrails never relax.

## Kill switches — three independent layers

### Layer 1 — Broker (the real one)
- **Revoke the Alpaca API key** in the Alpaca dashboard. The agent can no longer place,
  modify, or cancel anything, whatever state the VPS is in.
- **Flatten positions manually** in the Alpaca web/app.
- Keep the Alpaca login on your phone. Drill this before going live.

Note: revoking the key does **not** cancel broker-resident stop orders already placed —
that is intended; they keep protecting open positions.

### Layer 2 — Infrastructure (independent of the agent)
- **VPS provider dashboard**: power off or destroy the server from any browser.
- **SSH**: `sudo systemctl stop crown-agent`.
- **HALT file**: `/opt/crown/HALT`. The order gateway refuses every risk-adding order
  while it exists. Risk-reducing orders (exits, tighter stops) are still allowed, so a
  halt never strands an open position without protection.

### Layer 3 — Telegram (convenience only)
`/halt` writes the HALT file. It depends on the agent being healthy, so it is never the
emergency path.

## Code-enforced guardrails (order gateway)

Enforced in the deterministic wrapper around every order, regardless of what any upstream
component decided:

| Guardrail | Rule |
|---|---|
| Cash only | No order may use margin; buying power = **settled** cash minus reserve |
| No shorts | Sell orders may only reduce an existing long position |
| Options | Only structures in the account's `capabilities`; no naked short options; every options order is defined-risk |
| Instrument whitelist | Only symbols chosen by the substitution engine for an active plan |
| Order types | From the configured whitelist; market orders only for exits |
| Max notional per order | config (default 40% of sleeve) |
| Total open risk / per-thesis / cluster caps | doc 05 — rejected, not scaled, at the gateway |
| Max concurrent positions | config |
| Daily loss limit | realized + unrealized below threshold → write HALT, alert |
| Weekly loss limit | same, on a rolling 5-session window |
| Stop ratchet | a stop replacement that loosens is rejected |
| HALT file | blocks risk-adding orders |
| Duplicate / runaway check | no more than N orders per symbol per day; identical orders within M minutes rejected |

The LLM is told these limits exist so it doesn't propose around them; enforcement is
only in code.

## Independent watchdog

A separate, minimal process (OS cron, not the agent's scheduler) runs every 5 minutes:
- checks daily/weekly loss directly against Alpaca → writes HALT if breached;
- checks that every open position has either a broker-side stop or a recent agent-side
  monitor heartbeat → alerts if not;
- checks the agent's own heartbeat → alerts if stale.

It has read-only Alpaca keys plus permission to write HALT. It cannot trade.

## Graduated autonomy (D7)

Actions are split into **risk-reducing** and **risk-adding**.

- **Risk-reducing** — exits, trims, tightening a stop, cancelling an unfilled entry. These
  execute automatically at **every** autonomy level when they come from an approved plan,
  a Crown update, a monitor, or the HALT logic. A stop that waits for a phone reply is
  not a stop.
- **Risk-adding** — new entries, adds, increases. These need approval until the category
  has earned autonomy.

| Level | Risk-adding actions | Typical duration |
|---|---|---|
| **L0 — Approve everything** | Every entry needs an explicit Telegram yes. Approval request expires at the entry-window close (or 4 market hours) → **no trade**. | Paper soak + first live weeks |
| **L1 — Auto tier-0 ETF/equity** | Tier-0 entries in stocks/ETFs within caps execute automatically with a notification and a veto window (e.g. 15 min before submission). Everything else approved. | After L0 criteria met |
| **L2 — Auto tier-0/1 ETF/equity** | Adds instrument swaps / inverse ETFs / level translations. Options still approved. | After L1 criteria met |
| **L3 — Auto options (defined-risk)** | Options expressions within caps execute automatically. | After L2 criteria met |

Promotion criteria (per level, from the decision log — not from impression):
- ≥ N weeks at the current level with **zero** guardrail-relevant errors (wrong symbol,
  wrong side, wrong size, missed stop).
- Extraction audit: ≥ 98% of hand-checked fields correct over the period, no errors in
  levels/strikes/sizing.
- Your override rate on proposals in that category below a threshold (if you keep
  rejecting them, they aren't ready).

Demotion is automatic: any guardrail-relevant error in a category drops it back to L0 and
alerts.

Autonomy level lives in `config.yaml` per category and is read at every decision. The
system cannot raise its own level.

## LLM-specific safeguards

- The LLM never sees broker credentials and has no tool that submits orders.
- All LLM outputs are schema-validated; invalid output → human review, never a default.
- Prompt inputs from external content (letters, transcripts, web pages) are data. An
  instruction embedded in that content ("buy X now") has no effect because nothing the
  LLM outputs bypasses code decisions.

## Pre-live checklist

- [ ] Revoke-key drill done from phone in < 30 s (paper).
- [ ] VPS power-off from provider app tested.
- [ ] `systemctl stop crown-agent` tested over SSH.
- [ ] HALT blocks a risk-adding order and allows an exit (tested in paper).
- [ ] Daily and weekly loss limits auto-HALT (tested in paper).
- [ ] Watchdog alerts on a missing stop and a dead agent (tested).
- [ ] Settled-cash rule blocks a good-faith-violation sequence (tested).
- [ ] Stop-loosening rejection tested.
