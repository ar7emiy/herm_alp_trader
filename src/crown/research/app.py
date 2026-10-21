from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from crown.research.compiler import compile_records, load_sources
from crown.research.data import load_bars
from crown.research.models import D, ResearchPolicy, digest, serial
from crown.research.report import LIMITATIONS, write_report
from crown.research.runner import run


def policy_from_config(cfg, protective=False):
    """One explicit sizing path; percentage caps must follow the actual sleeve."""
    sleeve, ru, r = D(cfg.sizing.sleeve), D(cfg.sizing.risk_at_1_00), cfg.research
    common = dict(initial_cash=sleeve, risk_unit=ru,
                  reserve_fraction=D(r.reserve_fraction),
                  max_order_notional=D(cfg.guardrails.max_order_notional),
                  max_positions=cfg.guardrails.max_concurrent_positions,
                  daily_loss_limit=D(cfg.guardrails.daily_loss_limit),
                  weekly_loss_limit=sleeve*D(r.weekly_loss_fraction),
                  max_open_risk=sleeve*D(r.max_open_risk_fraction),
                  max_thesis_risk=ru*D(r.max_thesis_risk_units),
                  max_cluster_risk=sleeve*D(r.max_cluster_risk_fraction),
                  max_chase=D(cfg.no_chase.max_pct_from_trigger),
                  slippage_bps=D(r.slippage_bps), fee_bps=D(r.fee_bps),
                  max_letter_age_sessions=r.max_letter_age_sessions,
                  atr_multiple=D(r.atr_multiple), trail_multiple=D(r.trail_multiple))
    return ResearchPolicy.protective_research(**common) if protective else ResearchPolicy(**common)


def implementation_hashes():
    root = Path(__file__).resolve().parents[1]
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.py"))}


def backtest(cfg,start,end,policy_name="both",offline=True,out=None):
    folder=out or cfg.paths.reports/"replay"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder=folder.resolve()
    if not folder.is_relative_to(cfg.paths.reports.resolve()):
        raise RuntimeError("Replay output must stay under the private reports directory")
    if folder.exists():
        raise RuntimeError("Research output already exists; choose a new private output folder")
    if end<start:
        raise RuntimeError("Backtest end precedes start")
    from crown.data.calendar import market_open_close, trading_days
    sessions=trading_days(start,end)
    if not sessions or market_open_close(sessions[-1])[1]>datetime.now(timezone.utc):
        raise RuntimeError("Only completed historical NYSE sessions may be replayed")
    if cfg.backtest.approval_rate!=1:
        raise RuntimeError("Research requires an explicit deterministic approval rate of 1; no random approvals")
    policies={"strict":policy_from_config(cfg),"protective_research":policy_from_config(cfg,True)}
    chosen=list(policies.values()) if policy_name=="both" else [policies[policy_name]]
    sources,source_manifest=load_sources(cfg)
    batches={p.name:compile_records(sources,p,cfg.backtest.approval_latency_minutes) for p in chosen}
    symbols={plan.symbol for bs in batches.values() for b in bs for plan in b.plans
             if plan.allowed and b.available_at.date()<=end}
    bars,bar_manifest=load_bars(cfg,symbols,start,end,offline)
    implementation=implementation_hashes()
    manifest=serial({"format":"crown-research-v1","mode":"historical_research","orders_enabled":False,
                     "start":start,"end":end,"sources":source_manifest,"bars":bar_manifest,
                     "policies":chosen,"approval_latency_minutes":cfg.backtest.approval_latency_minutes,
                     "implementation_sha256":digest(implementation),"implementation_files":implementation,
                     "compiled_plans_sha256":digest(batches),
                     "entry_approval":"simulated deterministic research permission, not a live approval",
                     "limitations":LIMITATIONS,
                     "reference_coverage":{"stock_etf":"IEX daily raw","options":"unsupported",
                                           "futures_fx":"unsupported","macro_narrative":"unsupported"}})
    manifest_hash=digest(manifest)
    folder.mkdir(parents=True,exist_ok=False)
    (folder/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    results=[run(batches[p.name],bars,p,start,end,manifest_hash,folder/f"{p.name}.sqlite") for p in chosen]
    path=write_report(folder,manifest,results)
    return path,results
