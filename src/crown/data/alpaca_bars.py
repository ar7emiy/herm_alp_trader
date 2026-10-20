"""Historical bars from Alpaca's free-tier market data API, cached to
parquet so a backtest re-run costs zero API calls. Only historical data is
used here — paper trading keys are sufficient (03-build-plan.md Phase 1);
nothing in this module submits an order or touches the last-15-minutes
real-time restriction."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from crown.config import Config, alpaca_credentials
from crown.diagnostics import error_label, safe_error

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


def _cache_path(cfg: Config, cache_key: str, start: date, end: date) -> Path:
    cfg.paths.bars.mkdir(parents=True, exist_ok=True)
    return cfg.paths.bars / f"{cache_key}_{start}_{end}.parquet"


def _fetch_bars(
    symbols: list[str],
    start: date,
    end: date,
    cfg: Config,
    timeframe: TimeFrame,
    cache_suffix: str,
    force: bool,
) -> dict[str, pd.DataFrame]:
    symbols = sorted(set(symbols))
    result: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    for sym in symbols:
        path = _cache_path(cfg, f"{sym}_iex_raw{cache_suffix}", start, end)
        meta = path.with_suffix(".json")
        valid = False
        if path.exists() and meta.exists() and not force:
            try:
                metadata = json.loads(meta.read_text(encoding="utf-8"))
                expected = {"symbol": sym, "feed": "iex", "adjustment": "raw", "asof": "-",
                            "timeframe": str(timeframe), "start": str(start), "end_exclusive": str(end)}
                valid = (all(metadata.get(k) == v for k, v in expected.items())
                         and metadata.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest())
            except ValueError:
                pass
        if valid:
            result[sym] = pd.read_parquet(path)
        else:
            missing.append(sym)

    if not missing:
        return result

    key, secret = alpaca_credentials()
    client = StockHistoricalDataClient(key, secret)
    request = StockBarsRequest(
        symbol_or_symbols=missing,
        timeframe=timeframe,
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc),
        adjustment=Adjustment.RAW, feed=DataFeed.IEX, asof="-",
    )
    try:
        df = client.get_stock_bars(request).df
    except Exception as exc:
        raise RuntimeError(f"Historical bars failed: {error_label(safe_error(exc))}") from None

    for sym in missing:
        if df.empty or sym not in df.index.get_level_values(0):
            sym_df = pd.DataFrame(columns=BAR_COLUMNS)
            sym_df.index.name = "timestamp"
        else:
            sym_df = df.xs(sym, level=0)[BAR_COLUMNS].copy()
            sym_df.index = pd.to_datetime(sym_df.index, utc=True)
        path = _cache_path(cfg, f"{sym}_iex_raw{cache_suffix}", start, end)
        sym_df.to_parquet(path)
        path.with_suffix(".json").write_text(json.dumps({
            "symbol": sym, "feed": "iex", "adjustment": "raw", "asof": "-",
            "timeframe": str(timeframe), "start": str(start), "end_exclusive": str(end),
            "queried_at": datetime.now(timezone.utc).isoformat(), "rows": len(sym_df),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }, indent=2), encoding="utf-8")
        result[sym] = sym_df

    return result


def fetch_minute_bars(
    symbols: list[str], start: date, end: date, cfg: Config, force: bool = False,
) -> dict[str, pd.DataFrame]:
    """One DataFrame per symbol, indexed by UTC timestamp, columns
    open/high/low/close/volume — the primary series the zone-check engine
    and fill simulator replay against."""
    return _fetch_bars(symbols, start, end, cfg, TimeFrame.Minute, "", force)


def fetch_daily_bars(
    symbols: list[str], start: date, end: date, cfg: Config, force: bool = False,
) -> dict[str, pd.DataFrame]:
    """Daily bars — used for options expiry settlement (intrinsic value from
    the closing price on the expiry date), not for minute-by-minute replay."""
    return _fetch_bars(symbols, start, end, cfg, TimeFrame.Day, "_daily", force)
