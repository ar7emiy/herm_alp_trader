"""Phase 0 account probe (docs/02-constraints.md, "Phase 0 verification list").

Strictly READ-ONLY: every call here is a GET (account, configurations,
assets, option contracts, market data). Nothing submits, modifies, or
cancels an order, and nothing changes account configuration.

The probe reports what the account behind the current keys can do. With
paper keys that is the PAPER account, whose settings (margin, options
level) need not match the live cash account — the report says which one
was probed, and `capabilities.verified` in config.yaml should only be set
true once the live account's settings are confirmed (e.g. in the Alpaca
dashboard).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest, StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus
from alpaca.trading.requests import GetOptionContractsRequest

from crown.config import Config, alpaca_credentials, alpaca_is_paper

# Instruments the substitution engine is likely to need (docs/04): index and
# sector ETFs, -1x / leveraged inverses, commodity and currency ETFs, and a few
# single names Crown has traded. Extended at runtime with every instrument
# seen in cached extractions.
DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "SMH", "SOXX", "XLE", "XLF", "XLK", "XLV", "XLP", "IGV",
    "SH", "PSQ", "DOG", "RWM", "SEF", "TBF", "SDS", "QID", "SOXS", "GLL", "ZSL", "SCO",
    "GLD", "IAU", "SLV", "CPER", "USO", "BNO", "UNG", "TLT", "IEF", "SHY",
    "UUP", "UDN", "FXY", "FXE", "FXB", "FXA", "FXC",
    "MU", "MSFT", "META", "TSM", "RKLB",
]

# Underlyings used to test options listing and historical option bars over
# the letter period (2026-06 onward).
OPTION_TEST_UNDERLYINGS = ["GLD", "SPY", "SMH"]


def _safe(fn):
    try:
        return {"ok": True, "value": fn()}
    except Exception as e:  # noqa: BLE001 — the probe reports every failure, it never aborts
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _symbols_from_extractions(cfg: Config) -> list[str]:
    from crown.letter.inventory import load_all

    records, _ = load_all(cfg)
    syms: set[str] = set()
    for r in records:
        for p in r.extraction.crown_positions:
            syms.add(p.instrument.primary.upper())
            syms.update(a.upper() for a in p.instrument.alternatives)
    return sorted(s for s in syms if s.isalpha() and len(s) <= 5)


def run_probe(cfg: Config, letter_period_start: date | None = None) -> dict:
    key, secret = alpaca_credentials()
    paper = alpaca_is_paper()
    trading = TradingClient(key, secret, paper=paper)
    stocks = StockHistoricalDataClient(key, secret)
    options = OptionHistoricalDataClient(key, secret)
    start = letter_period_start or date.fromisoformat(cfg.backtest.start_date)

    report: dict = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "account_probed": "paper" if paper else "live",
    }

    # --- account -----------------------------------------------------------
    acct = _safe(trading.get_account)
    if acct["ok"]:
        a = acct["value"]
        report["account"] = {
            "status": str(a.status),
            "multiplier": a.multiplier,  # "1" = cash account behavior; >1 = margin
            "shorting_enabled": a.shorting_enabled,
            "pattern_day_trader": a.pattern_day_trader,
            "options_approved_level": a.options_approved_level,
            "options_trading_level": a.options_trading_level,
            "cash": a.cash,
            "buying_power": a.buying_power,
            "non_marginable_buying_power": a.non_marginable_buying_power,
            "options_buying_power": a.options_buying_power,
            "equity": a.equity,
            "trading_blocked": a.trading_blocked,
        }
    else:
        report["account"] = acct

    cfgs = _safe(trading.get_account_configurations)
    report["account_configurations"] = (
        cfgs["value"].model_dump(mode="json") if cfgs["ok"] else cfgs
    )

    # --- assets: tradable / fractionable / shortable ------------------------
    universe = sorted(set(DEFAULT_UNIVERSE) | set(_symbols_from_extractions(cfg)))
    assets = {}
    for sym in universe:
        res = _safe(lambda s=sym: trading.get_asset(s))
        if res["ok"]:
            x = res["value"]
            assets[sym] = {
                "tradable": x.tradable, "fractionable": x.fractionable,
                "shortable": x.shortable, "status": str(x.status),
                "exchange": str(x.exchange), "attributes": x.attributes,
            }
        else:
            assets[sym] = {"tradable": False, "error": res["error"]}
    report["assets"] = assets

    # --- options: listing + historical bars over the letter period ----------
    opt: dict = {}
    for und in OPTION_TEST_UNDERLYINGS:
        entry: dict = {}
        active = _safe(lambda u=und: trading.get_option_contracts(
            GetOptionContractsRequest(underlying_symbols=[u], limit=5)))
        entry["active_contracts_listed"] = (
            len(active["value"].option_contracts or []) if active["ok"] else active
        )
        expired = _safe(lambda u=und: trading.get_option_contracts(GetOptionContractsRequest(
            underlying_symbols=[u], status=AssetStatus.INACTIVE,
            expiration_date_gte=start + timedelta(days=14),
            expiration_date_lte=start + timedelta(days=45), limit=5)))
        if expired["ok"] and expired["value"].option_contracts:
            c = expired["value"].option_contracts[0]
            bars = _safe(lambda s=c.symbol: options.get_option_bars(OptionBarsRequest(
                symbol_or_symbols=s, timeframe=TimeFrame.Day,
                start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
                end=datetime.combine(c.expiration_date, datetime.min.time(), tzinfo=timezone.utc),
            )))
            entry["historical_contract"] = c.symbol
            entry["historical_daily_bars"] = (
                len(bars["value"].data.get(c.symbol, [])) if bars["ok"] else bars
            )
        else:
            entry["historical_contract"] = None
            entry["historical_lookup"] = expired if not expired["ok"] else "no expired contracts returned"
        opt[und] = entry
    report["options"] = opt

    # --- market data feeds -------------------------------------------------
    feeds = {}
    for feed in (DataFeed.IEX, DataFeed.SIP):
        q = _safe(lambda f=feed: stocks.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols="SPY", feed=f)))
        feeds[feed.value] = {"ok": True} if q["ok"] else q
    hist = _safe(lambda: stocks.get_stock_bars(StockBarsRequest(
        symbol_or_symbols="SPY", timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(start + timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc),
    )))
    feeds["historical_daily_bars_letter_period"] = (
        len(hist["value"].data.get("SPY", [])) if hist["ok"] else hist
    )
    report["market_data"] = feeds
    return report


def summarize(report: dict) -> list[str]:
    out = [f"Account probed: {report['account_probed'].upper()}"]
    a = report.get("account", {})
    if "multiplier" in a:
        kind = "cash-like (multiplier 1)" if str(a["multiplier"]) == "1" else f"MARGIN (multiplier {a['multiplier']})"
        out += [
            f"  type: {kind}; shorting_enabled={a['shorting_enabled']}; PDT={a['pattern_day_trader']}",
            f"  options approved/trading level: {a['options_approved_level']}/{a['options_trading_level']}",
            f"  cash={a['cash']} buying_power={a['buying_power']} non_marginable_bp={a['non_marginable_buying_power']}",
        ]
    else:
        out.append(f"  account: {a}")
    assets = report.get("assets", {})
    frac = sorted(s for s, v in assets.items() if v.get("fractionable"))
    nonfrac = sorted(s for s, v in assets.items() if v.get("tradable") and not v.get("fractionable"))
    untradable = sorted(s for s, v in assets.items() if not v.get("tradable"))
    out += [f"  fractionable ({len(frac)}): {' '.join(frac)}",
            f"  tradable, NOT fractionable ({len(nonfrac)}): {' '.join(nonfrac)}",
            f"  not tradable / not found ({len(untradable)}): {' '.join(untradable)}"]
    for und, o in report.get("options", {}).items():
        out.append(f"  options {und}: active listed={o.get('active_contracts_listed')}, "
                   f"historical {o.get('historical_contract')} bars={o.get('historical_daily_bars', o.get('historical_lookup'))}")
    md = report.get("market_data", {})
    out.append(f"  data feeds: iex={'ok' if md.get('iex', {}).get('ok') else md.get('iex')}, "
               f"sip={'ok' if md.get('sip', {}).get('ok') else 'unavailable'}; "
               f"SPY daily bars in letter period={md.get('historical_daily_bars_letter_period')}")
    if report["account_probed"] == "paper":
        out.append("  NOTE: paper-account settings may differ from your live cash account. Confirm the "
                   "live account's options level in the Alpaca dashboard before setting capabilities.verified.")
    return out


def write_probe(cfg: Config, report: dict) -> Path:
    cfg.paths.probe.mkdir(parents=True, exist_ok=True)
    path = cfg.paths.probe / f"probe-{report['account_probed']}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
