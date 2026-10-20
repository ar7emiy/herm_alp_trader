"""Offline-first historical input with feed provenance and closed-bar timing."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pandas as pd

from crown.data.calendar import market_open_close, trading_days
from crown.research.models import Bar, D


def load_bars(cfg, symbols, start, end, offline=True):
    begin = start-timedelta(days=45)
    finish = end+timedelta(days=1)
    if not offline:
        from crown.data.alpaca_bars import fetch_daily_bars
        fetch_daily_bars(sorted(symbols), begin, finish, cfg)
    bars, inputs = {}, []
    for sym in sorted(symbols):
        # Select a single covering cache; never stitch incompatible vintages.
        candidates = []
        for meta in cfg.paths.bars.glob(f"{sym}_iex_raw_daily_*.json"):
            m = json.loads(meta.read_text(encoding="utf-8"))
            path = meta.with_suffix(".parquet")
            if (m.get("symbol")==sym and m.get("feed")=="iex" and m.get("adjustment")=="raw"
                    and m.get("asof")=="-" and m.get("timeframe")=="1Day"
                    and m.get("start", "9999")<=str(begin) and m.get("end_exclusive", "")>=str(finish)
                    and path.exists() and m.get("sha256")==hashlib.sha256(path.read_bytes()).hexdigest()):
                candidates.append((m["queried_at"],path,m))
        if not candidates:
            raise RuntimeError(f"Missing hash-validated IEX raw daily cache for {sym}; run without --offline to fetch GET data")
        _, path, m = max(candidates, key=lambda v:v[0])
        df = pd.read_parquet(path)
        if not df.index.is_unique:
            raise RuntimeError(f"Duplicate daily bars for {sym}")
        series = {}
        for stamp, row in df.sort_index().iterrows():
            if stamp.tzinfo is None:
                raise RuntimeError("Naive bar timestamp")
            day = stamp.tz_convert("America/New_York").date()
            if not begin<=day<=end:
                continue
            hours = market_open_close(day)
            if not hours or day in series:
                raise RuntimeError("Invalid daily session labels")
            series[day]=Bar(sym,day,*hours,*(D(row[k]) for k in ("open","high","low","close","volume")))
        bars[sym]=series
        inputs.append({k:m[k] for k in ("symbol","feed","adjustment","asof","timeframe","start","end_exclusive","queried_at","sha256")})
    return bars, inputs


def prior_atr(series: dict, day, sessions=14):
    """Simple mean true range, using consecutive fully CLOSED prior sessions.

    SMA is fixed before evaluation; not tuned on this sample. Missing sessions
    invalidate the warmup instead of shrinking the lookback through a data hole.
    """
    days=trading_days(day-timedelta(days=60), day)
    previous=[d for d in days if d<day][-sessions-1:]
    if len(previous)<sessions+1 or any(d not in series for d in previous):
        return None
    ranges=[]
    for a,b in zip(previous, previous[1:]):
        before, bar=series[a],series[b]
        ranges.append(max(bar.high-bar.low,abs(bar.high-before.close),abs(bar.low-before.close)))
    return sum(ranges,D(0))/D(sessions)
