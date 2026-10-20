"""Private standalone results. Source material and reports are never public assets."""
from __future__ import annotations

import json
from html import escape

from crown.research.models import serial


LIMITATIONS = [
    "Historical research only. No broker orders; not evidence of autonomous paper readiness or future returns.",
    "Unknown receipt times use next NYSE open after the printed date plus configured simulated approval latency (default 45 minutes). Daily execution then waits for the next open. Receipt precision is not established.",
    "Two independent calls use the same model and can share errors. Most letters are not manually audited. Historical model knowledge can leak hindsight; this is not an out-of-sample causal performance test.",
    "Only identical plain long stocks/ETFs are supported. Options, pairs, futures, FX and unmonitorable invalidations are explicit skips. This is a selective feasible book, not Crown's full portfolio.",
    "IEX single-venue raw daily bars are not consolidated executable quotes. Fill simulation uses 5 bps slippage and 1 bp rounded-up fees on each side; spreads and market impact are not observed.",
    "Intraday order is unknown: stop precedes target on the same bar. Close-based exits fill next open. ATR uses 14 prior consecutive sessions; new trailing stops apply next session. Research touch protection never lowers a printed numerical stop, even when its convention is ambiguous.",
    "Raw prices exclude dividends/distributions and do not supply verified corporate actions. A >40% open/previous-close discontinuity aborts the run; smaller actions may remain. P&L is price-only, not total return.",
    "Initial cash is the scenario policy's initial_cash ($5,000 in the baseline). Sales become reusable on the next NYSE session that is also a federal banking day. This conservative model is not verified broker settlement reporting.",
    "All supported assets share one conservative thesis and risk cluster until semantic cross-instrument linking is verified. Risk to stop is an estimate; gap losses can exceed the risk budget. Missing evidence triggers exits rather than assuming Crown sold.",
    "Protective research adds unapproved ATR/fractional/timing overlays, tagged Tier 2 and shadow only. Policies are fixed comparisons, not optimized parameters or live permissions.",
    "The strict book may hold cash throughout. Comparing it with the selective protective book is not Crown-replica benchmarking, causal overlay attribution, or evidence that a policy is optimal.",
]


def write_report(folder, manifest, results):
    folder.mkdir(parents=True,exist_ok=True)
    payload=serial({"manifest":manifest,"limitations":LIMITATIONS,"results":results})
    (folder/"results.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    (folder/"manifest.json").write_text(json.dumps(serial(manifest),indent=2),encoding="utf-8")
    data=json.dumps(payload).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    summaries="".join(f'<tr><td>{escape(r["policy"]["name"])}</td><td>${float(r["final_equity"]):,.2f}</td><td>${float(r["net_pnl"]):,.2f}</td><td>{float(r["max_drawdown_pct"]):.2f}%</td><td>{r["counts"].get("entry_fill",0)}</td><td>{r["counts"].get("exit_fill",0)}</td><td>{r["counts"].get("skip",0)}</td></tr>' for r in results)
    text='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crown research replay</title><style>
:root{color-scheme:dark;font-family:system-ui;background:#10161f;color:#edf1f7}body{max-width:1240px;margin:36px auto;padding:0 22px}h1{font-size:30px}p,li{line-height:1.6;color:#bdc9d9}.badge{color:#ffcf7a}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #334155}select,input{background:#202b3b;color:inherit;border:1px solid #546477;padding:10px;margin:12px 8px 12px 0}svg{background:#162131;width:100%;height:240px}details{border-bottom:1px solid #334155;padding:12px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}a{color:#8cbeff}button{padding:10px;background:#26384e;color:inherit;border:1px solid #52667f}small{color:#a7b7ca}</style>
<h1>Crown / cash-account research</h1><p class="badge">Historical research · broker execution disabled · private source material</p>
<p id="range"></p><table><thead><tr><th>Fixed policy</th><th>Ending equity</th><th>Net price P&amp;L</th><th>Drawdown</th><th>Entries</th><th>Exits / trims</th><th>Skip decisions</th></tr></thead><tbody>SUMMARY</tbody></table>
<h2>Equity and decisions</h2><select id="policy" aria-label="Policy"></select><input id="filter" aria-label="Search decisions" placeholder="Search symbol, reason, or date"><p><small>Modeled equity includes open positions and unsettled sale proceeds. No forced end-of-test liquidation.</small></p><svg viewBox="0 0 1000 240" role="img" aria-label="Historical modeled equity"><polyline id="line" fill="none" stroke="#71cbb7" stroke-width="2"/><text id="bounds" x="20" y="25" fill="#bdc9d9"/></svg><p id="count"></p><div id="events"></div><button id="more">Show 100 more</button>
<h2>Interpretation and limits</h2><ul>LIMITS</ul><details><summary>Frozen inputs and policy hashes</summary><pre id="manifest"></pre></details>
<script id="payload" type="application/json">DATA</script><script>
const data=JSON.parse(document.getElementById('payload').textContent), select=document.getElementById('policy');let limit=100;
data.results.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.policy.name;select.append(o)});
document.getElementById('range').textContent=data.results[0].start+' through '+data.results[0].end+' | '+data.manifest.sources.length+' frozen source letters';
document.getElementById('manifest').textContent=JSON.stringify(data.manifest,null,2);
function draw(){const r=data.results[+select.value], values=r.equity_curve.map(x=>+x.equity),lo=Math.min(+r.initial_equity,...values),hi=Math.max(+r.initial_equity,...values),span=hi-lo||1;document.getElementById('line').setAttribute('points',values.map((v,i)=>(20+i*960/Math.max(1,values.length-1))+','+(210-(v-lo)*160/span)).join(' '));document.getElementById('bounds').textContent='$'+lo.toFixed(2)+' – $'+hi.toFixed(2);const q=document.getElementById('filter').value.toLowerCase(),ev=r.events.filter(e=>JSON.stringify(e).toLowerCase().includes(q));document.getElementById('count').textContent=ev.length+' matching decisions | journal '+r.journal_sha256;const root=document.getElementById('events');root.replaceChildren();ev.slice(0,limit).forEach(e=>{const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');s.textContent=e.at.slice(0,10)+' · '+e.kind+' · '+(e.symbol||'Portfolio')+' · '+e.reason;p.textContent=JSON.stringify(e,null,2);d.append(s,p);root.append(d)});document.getElementById('more').hidden=ev.length<=limit}
select.onchange=()=>{limit=100;draw()};document.getElementById('filter').oninput=()=>{limit=100;draw()};document.getElementById('more').onclick=()=>{limit+=100;draw()};draw();
</script></html>'''
    text=text.replace("SUMMARY",summaries).replace("LIMITS","".join(f"<li>{escape(x)}</li>" for x in LIMITATIONS)).replace("DATA",data)
    path=folder/"index.html"
    path.write_text(text,encoding="utf-8")
    return path
