# 11 — Runbook

> 2026-10-19 launch clarification: the intended paper release can run unattended within
> an explicit owner-set category policy (doc 13). Live entries remain governed by doc 07.
> A future live launch requires a separate explicit configuration/authorization gate;
> changing keys alone must never switch the system into live trading.

Current implementation: use the offline historical commands in
[doc 15](15-research-replay.md). The normal-week service, broker protection, approvals,
watchdog and VPS procedures below describe the intended paper system; they are not yet
running. `crown backtest` is now v3 research; old commands require the `legacy-` prefix.

## Normal week

- **When a letter lands (usually Monday, sometimes mid-week):** the system extracts,
  reconciles, and plans. You get one memo per decision that needs you (entries at L0) and
  a summary of everything else (skips, Crown closes we executed, stops moved).
- **During the week:** monitors run silently. You hear about entries triggering, exits
  executed, theses weakened/invalidated, and anything flagged for review.
- **Friday after close:** weekly scorecard — Crown vs us, deviations, skips, attribution.

## Weekly review (mandatory during paper soak)

- [ ] Spot-check the extraction against the letter pages (quotes have page numbers).
- [ ] Read every decision record for entries, exits, and skips. Agree with each "why"?
- [ ] Any positions we hold that Crown doesn't (or vice versa)? Expected?
- [ ] Any agent-side-only stops? Still necessary?
- [ ] Scorecard + attribution: which constraint cost the most this week?
- [ ] Any review flags unresolved?

## When something looks wrong

Escalate in this order — do not start with Telegram:

1. **A bad trade is about to happen or did** → revoke the Alpaca API key (Layer 1), then
   flatten manually if needed. Broker-side stops stay in place.
2. **Agent looping / spamming / unresponsive** → power off the VPS from the provider
   dashboard, or `sudo systemctl stop crown-agent` over SSH (Layer 2).
3. **Just want to pause** → `touch /opt/crown/HALT` over SSH, or Telegram `/halt`
   (Layer 3). Exits still work while halted.

After any halt, read the decision log before restarting. Find the root cause first.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Letter not processed | Gmail auth expired; letter late; new sender address | Re-auth; check inbox; update sender filter |
| Extraction flagged / validator failures | Layout drift; misread numbers in images | Review flagged fields against pages; fix and re-run extraction for that letter |
| Position not linked to prior letter | Crown renamed / restructured the trade | Link manually in review; add the case to linker tests |
| Order rejected by gateway | A guardrail fired | Working as intended; read the reason in the decision record |
| Good-faith-violation warning from Alpaca | Settled-cash tracking off | HALT; reconcile cash with Alpaca; fix tracking before resuming |
| Watchdog: position without stop | Broker stop cancelled/rejected; fractional position | Re-place stop; check order type support |
| Approval timeouts piling up | You're unavailable | Expected → no trade. Consider L1 for tier-0 ETFs once criteria are met |
| LLM errors / schema failures | API outage or model change | Pipeline holds; nothing trades on a failed output. Pin model ids in config |

## Mode discipline

- Paper→live requires an explicit endpoint and permission gate as well as credentials.
  Changing keys alone must never enable live trading.
- Never disable guardrails to "let it try something."
- Autonomy changes only through the doc 07 promotion criteria, recorded in config with a date.

## Reminder

Crown's letter is educational opinion and can be wrong; extraction can misread an image;
markets gap. The system exists to express Crown's ideas faithfully within your
constraints and to show you exactly where it couldn't — not to trade unattended while
nobody looks.
