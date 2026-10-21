"""Bounded offline strategy experiments; no order client, LLM loop or auto-promotion.

Every variant has an isolated $5,000 account. The recipe and inputs are written
before simulation. Previously inspected history is exploratory, never a holdout.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html import escape
import json

from crown.data.calendar import market_open_close, trading_days
from crown.research.app import implementation_hashes, policy_from_config
from crown.research.compiler import compile_records, load_sources
from crown.research.coverage import coverage_report
from crown.research.data import load_bars
from crown.research.models import D, ResearchPolicy, digest, serial
from crown.research.report import write_report
from crown.research.runner import run


@dataclass(frozen=True)
class Experiment:
    policy: ResearchPolicy
    approval_minutes: int
    hypothesis: str


def experiments(cfg):
    """Small ablation set, not a Cartesian parameter search or fitted optimum."""
    if D(cfg.sizing.sleeve) != D(5000):
        raise RuntimeError("The research lab requires a $5,000 sleeve for every independent budget")
    base = policy_from_config(cfg, True)
    delay = cfg.backtest.approval_latency_minutes
    return [
        Experiment(policy_from_config(cfg), delay, "Published terms only; feasibility control, not a Crown replica."),
        Experiment(base, delay, "Existing protective research control; all fidelity gates retained."),
        Experiment(replace(base, name="half_risk", risk_unit=base.risk_unit/2), delay,
                   "Reduce risk per Crown unit by half; unchanged source, direction, cash and portfolio caps."),
        Experiment(replace(base, name="atr_tighter", atr_multiple=D('1.5')), delay,
                   "Tighter fallback ATR stop; measure turnover and losses without moving a printed stop lower."),
        Experiment(replace(base, name="atr_wider", atr_multiple=D(3)), delay,
                   "Wider fallback before entry only; still capped by any printed numeric stop. Stops never loosen after entry."),
        Experiment(replace(base, name="trail_tighter", trail_multiple=D(2)), delay,
                   "Change only trailing distance after +1R; completed-close signal applies next session."),
        Experiment(replace(base, name="trail_wider", trail_multiple=D(4)), delay,
                   "Alternative trail distance, with the same ratchet and initial protective rules."),
        Experiment(replace(base, name="cost_stress", slippage_bps=max(D(15), base.slippage_bps*3),
                           fee_bps=max(D(2), base.fee_bps*2)), delay,
                   "Stress modeled execution costs; not a candidate strategy."),
        Experiment(replace(base, name="zero_approval_delay"), 0,
                   "Remove approval latency only. Unknown receipts still use next session after issue; daily data cannot model instant intraday fills."),
    ]


def metrics(result):
    """Aggregate partial exits by episode so trims cannot inflate sample size."""
    entries = {e["position_id"] for e in result["events"] if e["kind"] == "entry_fill"}
    opened = {p["position_id"] for p in result["open_positions"]}
    closed = entries - opened
    exposure_days = {e["at"][:10] for e in result["events"] if e["kind"] in {"entry_fill", "exit_fill"}}
    exposure_days.update(p["day"] for p in result["equity_curve"] if p["positions"])
    fees = sum((D(e.get("fee", 0)) for e in result["events"] if e["kind"] in {"entry_fill", "exit_fill"}), D(0))
    return {"entries": len(entries), "closed_episodes": len(closed), "exposed_sessions": len(exposure_days),
            "open_episodes": len(opened), "modeled_fees": str(fees),
            "net_pnl": result["net_pnl"], "return_pct": result["return_pct"],
            "max_drawdown_pct": result["max_drawdown_pct"]}


def summarize(results, split_day):
    summaries = []
    for result in results:
        curve = result["equity_curve"]
        before = [p for p in curve if p["day"] <= str(split_day)]
        boundary = D(before[-1]["equity"]) if before else D(result["initial_equity"])
        first = boundary - D(result["initial_equity"])
        second = D(result["final_equity"]) - boundary
        summaries.append({"name": result["policy"]["name"], **metrics(result),
                          "first_segment_pnl": str(first), "second_segment_pnl": str(second),
                          "split_note": "Continuous book with mark-to-market allocation across split; not independent folds or unseen data."})
    return summaries


def promotion_assessment(summaries):
    # Previously examined letters cannot become an untouched sample by splitting.
    largest = max((s["closed_episodes"] for s in summaries), default=0)
    return {"selected_strategy": None, "promotion_allowed": False,
            "status": "exploratory_only", "largest_closed_episode_sample": largest,
            "reasons": ["Historical corpus has already been inspected; no untouched forward validation.",
                        "No recipe can authorize broker trading or raise autonomy.",
                        "More variants of the same trades are not more independent observations."] +
                       (["Fewer than 20 completed episodes even in the largest sample; diagnostic floor, not a statistical sufficiency claim."] if largest < 20 else [])}


def laboratory(cfg, start, end, out=None, progress=None):
    """Offline by construction. Inputs must already be present and hash validated."""
    sessions = trading_days(start, end) if start <= end else []
    if len(sessions) < 2 or market_open_close(sessions[-1])[1] > datetime.now(timezone.utc):
        raise RuntimeError("Lab requires at least two completed historical NYSE sessions")
    if cfg.backtest.approval_rate != 1:
        raise RuntimeError("Lab requires deterministic simulated approval rate 1")
    folder = (out or cfg.paths.reports/"lab"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    if not folder.is_relative_to(cfg.paths.reports.resolve()):
        raise RuntimeError("Lab output must stay under the private reports directory")
    if folder.exists():
        raise RuntimeError("Lab output already exists; experiments are never overwritten")
    recipe = experiments(cfg)
    from crown.research.walkforward import evaluate_walkforward, MIN_TRAIN_EPISODES
    sources, source_manifest = load_sources(cfg)
    batches = {e.policy.name: compile_records(sources, e.policy, e.approval_minutes) for e in recipe}
    symbols = {p.symbol for bs in batches.values() for b in bs if b.available_at.date() <= end
               for p in b.plans if p.allowed}
    bars, bar_manifest = load_bars(cfg, symbols, start, end, offline=True)
    coverage = coverage_report(sources, batches["protective_research"], end)
    implementation = implementation_hashes()
    split = sessions[len(sessions)//2-1]
    manifest = serial({"format": "crown-lab-v1", "orders_enabled": False, "paper_only": True,
                       "mode": "exploratory_historical_research", "start": start, "end": end,
                       "registered_at": datetime.now(timezone.utc), "experiments": recipe,
                       "budget_per_experiment": 5000, "independent_ledgers": True,
                       "funding_note": "Nine hypothetical alternatives, not nine broker subaccounts or a combined funded portfolio.",
                       "sources": source_manifest, "bars": bar_manifest, "split_day": split,
                       "compiled_plans_sha256": digest(batches), "coverage_sha256": digest(coverage),
                       "implementation_files": implementation, "implementation_sha256": digest(implementation),
                       "selection": "No historical winner promoted. All hypotheses and outcomes retained, including failures."})
    manifest["walkforward"] = {"initial_training_sessions":40,"validation_sessions":20,"embargo_sessions":5,
                               "minimum_closed_episodes":MIN_TRAIN_EPISODES,"training_max_drawdown_pct":6,
                               "require_positive_cost_stress":True,"folds_reset_cash":True}
    folder.mkdir(parents=True, exist_ok=False)
    # Registration is persisted BEFORE the first replay and retained on failure.
    registration = folder/"registration.json"
    registration.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest_hash = digest(manifest)
    results = []
    try:
        for experiment in recipe:
            p = experiment.policy
            if progress:
                progress(f"Simulating {p.name}: isolated $5,000 ledger")
            result = run(batches[p.name], bars, p, start, end, manifest_hash, folder/f"{p.name}.sqlite")
            results.append(result)
        summaries = summarize(results, split)
        assessment = promotion_assessment(summaries)
        wf_folder = folder/"walkforward"
        wf_folder.mkdir()
        walkforward = evaluate_walkforward(sources,bars,recipe,start,end,wf_folder,manifest_hash,progress)
        (wf_folder/"results.json").write_text(json.dumps(walkforward,indent=2),encoding="utf-8")
        payload = serial({"manifest_sha256": manifest_hash, "summaries": summaries,
                          "assessment": assessment, "coverage": coverage, "walkforward":walkforward})
        (folder/"study.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        write_report(folder/"replays", manifest, results)
        write_lab_report(folder, manifest, payload)
        (folder/"completion.json").write_text(json.dumps({"status": "complete", "results_sha256": digest(results),
                                                        "study_sha256": digest(payload)}, indent=2), encoding="utf-8")
    except Exception as exc:
        # Do not expose provider exception bodies or turn failed trials into successes.
        (folder/"completion.json").write_text(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                                                        "completed_experiments": len(results)}), encoding="utf-8")
        raise
    return folder/"index.html", payload


def write_lab_report(folder, manifest, study):
    rows = "".join(
        f'<tr><td>{escape(s["name"])}</td><td>$5,000</td><td>{s["entries"]}</td>'
        f'<td>{s["closed_episodes"]}</td><td>${float(s["net_pnl"]):,.2f}</td>'
        f'<td>{float(s["return_pct"]):.2f}%</td><td>{float(s["max_drawdown_pct"]):.2f}%</td>'
        f'<td>${float(s["first_segment_pnl"]):,.2f} / ${float(s["second_segment_pnl"]):,.2f}</td></tr>'
        for s in study["summaries"])
    blockers = "".join(f'<li>{escape(k)}: {v} weekly rows</li>' for k,v in study["coverage"]["blocker_counts"].items())
    assessments = "".join(f'<li>{escape(r)}</li>' for r in study["assessment"]["reasons"])
    hypotheses = "".join(f'<li><strong>{escape(e["policy"]["name"])}</strong>: {escape(e["hypothesis"])}</li>' for e in manifest["experiments"])
    folds = "".join(f'<li>Train through {f["train_end"]}; test {f["test_start"]}–{f["test_end"]}: '
                    f'{escape(f["chosen"] or "cash — no eligible candidate")}</li>' for f in study["walkforward"]["folds"])
    payload = json.dumps(study["coverage"]["rows"]).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crown research laboratory</title><style>
:root{color-scheme:dark;font-family:system-ui;background:#10161f;color:#edf1f7}body{max-width:1280px;margin:32px auto;padding:0 24px}p,li{line-height:1.6;color:#bdc9d9}a{color:#8cbeff}.badge{color:#ffcf7a}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td,th{padding:12px;text-align:left;border-bottom:1px solid #334155}.scroll{overflow-x:auto}details{border-bottom:1px solid #334155;padding:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}input{padding:12px;min-width:260px;background:#202b3b;color:inherit;border:1px solid #546477}</style>
<h1>Crown research laboratory</h1><p class="badge">Paper only · isolated $5,000 experiments · broker orders disabled</p>
<p>START through END. Each row has its own cash, risk reservations and journal. Alternatives share the same historical opportunities; their results are not independent evidence and must not be summed as one account.</p>
<div class="scroll"><table><thead><tr><th>Experiment</th><th>Budget</th><th>Entries</th><th>Closed episodes</th><th>Net price P&amp;L</th><th>Return</th><th>Drawdown</th><th>First / second segment P&amp;L</th></tr></thead><tbody>ROWS</tbody></table></div>
<p>Segment split: SPLIT. Continuous positions are marked at the boundary. Both segments are previously inspected research, not an untouched holdout. Price-only returns omit distributions and use modeled fills.</p>
<p><a href="replays/index.html">Inspect equity curves, exact policies and every decision</a> · <a href="registration.json">Frozen experiment registration</a> · <a href="study.json">Full study data</a></p>
<h2>No strategy promoted</h2><ul>ASSESSMENTS</ul><details><summary>What each experiment tests</summary><ul>HYPOTHESES</ul></details>
<h2>Chronological selection check</h2><p>Training uses only earlier available letters and bars. Five sessions separate training and validation. Each fold starts flat with $5,000; no results are compounded. History has already been inspected, so these are exploratory checks.</p><ul>FOLDS</ul><p><a href="walkforward/results.json">Training scores, selection reasons and validation results</a></p>
<h2>Coverage before optimization</h2><p>Overlapping blockers count weekly rows, not unique bets or lost profits.</p><ul>BLOCKERS</ul>
<h2>Crown reasoning and unresolved implementation</h2><p>Claims, evidence, drivers and invalidations are cached extractions tied to source pages. They may contain errors. Missing proprietary analytics remain unknown; no inferred thesis is traded.</p>
<input id="filter" aria-label="Search Crown evidence" placeholder="Search symbol, thesis or blocker"><p id="count"></p><div id="dossiers"></div>
<script id="payload" type="application/json">PAYLOAD</script><script>
const rows=JSON.parse(document.getElementById('payload').textContent);
function draw(){const q=document.getElementById('filter').value.toLowerCase(),visible=rows.filter(r=>JSON.stringify(r).toLowerCase().includes(q)),root=document.getElementById('dossiers');root.replaceChildren();document.getElementById('count').textContent=visible.length+' weekly position records';visible.forEach(r=>{const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');s.textContent=r.letter_date+' · '+r.symbol+' · '+r.structure+' · '+(r.allowed?'eligible for research':r.categories.join(', '));p.textContent=JSON.stringify(r,null,2);d.append(s,p);root.append(d)})};document.getElementById('filter').oninput=draw;draw();</script></html>'''
    for key,value in {"START":manifest["start"],"END":manifest["end"],"SPLIT":manifest["split_day"],
                      "ROWS":rows,"ASSESSMENTS":assessments,"HYPOTHESES":hypotheses,"BLOCKERS":blockers,"FOLDS":folds}.items():
        html = html.replace(key,str(value))
    (folder/"index.html").write_text(html.replace("PAYLOAD",payload), encoding="utf-8")
