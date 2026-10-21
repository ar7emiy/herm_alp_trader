"""Chronological, bounded research selection with an explicit insufficient-data state.

Walk-forward structure alone does not make this previously inspected corpus an
untouched test. Fold books start flat; old holdings are not invented at a fold
boundary. No selection is a permission to execute at a broker.
"""
from dataclasses import replace

from crown.data.calendar import market_open_close, trading_days
from crown.research.compiler import availability, compile_records
from crown.research.models import D, digest, serial
from crown.research.runner import run


MIN_TRAIN_EPISODES = 20  # Diagnostic floor only, not a proof of significance.


def folds(start, end, train_sessions=40, test_sessions=20, embargo_sessions=5):
    if train_sessions < 2 or test_sessions < 1 or embargo_sessions < 1:
        raise ValueError("Training, testing and a positive embargo are required")
    days = trading_days(start, end)
    result = []
    index = train_sessions+embargo_sessions
    while index < len(days):
        result.append((days[0], days[index-embargo_sessions-1], days[index], days[min(index+test_sessions-1,len(days)-1)]))
        index += test_sessions
    return result


def select_training(rows, minimum=MIN_TRAIN_EPISODES):
    """Cost-stressed gain with bounded drawdown; no maximizing in-sample Sharpe."""
    eligible = [r for r in rows if r["closed_episodes"] >= minimum
                and r["stress_tested"]
                and D(r["net_pnl"]) > 0 and D(r["stress_net_pnl"]) > 0
                and D(r["max_drawdown_pct"]) <= D(6)]
    if not eligible:
        return None
    # Lower drawdown wins, then stressed return; name makes ties deterministic.
    return min(eligible, key=lambda r: (D(r["max_drawdown_pct"]), -D(r["stress_net_pnl"]),r["name"]))["name"]


def evaluate_walkforward(sources, bars, recipe, start, end, folder, manifest_hash, progress=None):
    from crown.research.lab import metrics  # Avoid import cycle at module load.
    candidates = [e for e in recipe if e.policy.name not in {"strict", "cost_stress", "zero_approval_delay"}]
    records = []
    for index, (train_start, train_end, test_start, test_end) in enumerate(folds(start, end)):
        if progress:
            progress(f"Chronological fold {index+1}: train through {train_end}, test from {test_start}")
        training_sources = [s for s in sources if availability(s[0],0)[0] <= market_open_close(train_end)[1]]
        training_bars = {sym: {d:b for d,b in series.items() if d<=train_end} for sym,series in bars.items()}
        rows = []
        for experiment in candidates:
            p = experiment.policy
            batches = compile_records(training_sources,p,experiment.approval_minutes)
            trained = run(batches,training_bars,p,train_start,train_end,manifest_hash,
                          folder/f"fold{index+1}_{p.name}_train.sqlite")
            m = metrics(trained)
            stressed = None
            if m["closed_episodes"] >= MIN_TRAIN_EPISODES and D(m["net_pnl"]) > 0:
                stress = replace(p,slippage_bps=max(D(15),p.slippage_bps*3),fee_bps=max(D(2),p.fee_bps*2))
                stressed = run(batches,training_bars,stress,train_start,train_end,manifest_hash,
                               folder/f"fold{index+1}_{p.name}_stress.sqlite")["net_pnl"]
            rows.append({"name":p.name, **m, "stress_net_pnl": stressed or "0",
                         "stress_tested": stressed is not None, "training_journal_sha256":trained["journal_sha256"]})
        chosen = select_training(rows)
        selection = {"fold":index+1,"train_start":str(train_start),"train_end":str(train_end),
                     "test_start":str(test_start),"test_end":str(test_end),"training":rows,"chosen":chosen,
                     "reason":"Training criteria met; research only" if chosen else "Insufficient positive, cost-stressed training evidence; stay in cash",
                     "known_history":True,"fold_initial_cash":"5000","manifest_sha256":manifest_hash}
        # Persist selection before seeing any validation performance.
        import json
        (folder/f"fold{index+1}_selection.json").write_text(json.dumps(selection,indent=2),encoding="utf-8")
        if chosen:
            experiment = next(e for e in candidates if e.policy.name==chosen)
            all_sources = [s for s in sources if availability(s[0],0)[0]<=market_open_close(test_end)[1]]
            # Fresh source updates only in the validation interval. No fabricated
            # opening positions or carry from the separately funded training book.
            bs = [b for b in compile_records(all_sources,experiment.policy,experiment.approval_minutes)
                  if market_open_close(test_start)[0]<=b.available_at<=market_open_close(test_end)[1]]
            validation_bars = {s:{d:b for d,b in series.items() if d<=test_end} for s,series in bars.items()}
            result = run(bs,validation_bars,experiment.policy,test_start,test_end,digest(selection),
                         folder/f"fold{index+1}_validation.sqlite")
            selection["validation"] = metrics(result)
        else:
            selection["validation"] = {"net_pnl":"0","entries":0,"reason":"Cash; no eligible training winner"}
        records.append(selection)
    return serial({"folds":records,"minimum_training_closed_episodes":MIN_TRAIN_EPISODES,
                   "schedule":{"initial_training_sessions":40,"validation_sessions":20,"embargo_sessions":5},
                   "limitations":["Previously inspected history; exploratory algorithm validation, not a claim of untouched out-of-sample returns.",
                                  "Every fold resets $5,000 cash; results are not a continuous funded strategy and are not compounded.",
                                  "No open training book carries into validation. Only new source availability in validation can enter.",
                                  "Minimum sample and drawdown thresholds are coarse research rules, not statistical significance."]})
