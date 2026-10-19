"""Phase 0 account probe (docs/02-constraints.md, "Phase 0 verification list").

Strictly READ-ONLY: every call here is a GET (account, configurations,
assets, option contracts, market data). Nothing submits, modifies, or
cancels an order, and nothing changes account configuration.

This command is restricted to the PAPER endpoint. Its observations do not
certify live capabilities, legal account classification, settled cash,
order acceptance, or data freshness. It never changes capabilities or
infers a cash account from a buying-power multiplier.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import NewsClient, OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import NewsRequest, OptionBarsRequest, StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus
from alpaca.trading.requests import GetOptionContractsRequest

from crown.config import Config, alpaca_credentials, alpaca_is_paper
from crown.diagnostics import error_label, safe_error

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
    except Exception as exc:
        return safe_error(exc)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _timestamp(value) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _quote_snapshot(quotes, symbol: str, now: datetime) -> dict:
    quote = _field(quotes, symbol)
    if quote is None:
        return {"ok": False, "error_type": "MissingQuote"}
    at = _timestamp(_field(quote, "timestamp"))
    return {
        "ok": True,
        "symbol": symbol,
        "bid": _field(quote, "bid_price"),
        "ask": _field(quote, "ask_price"),
        "timestamp": at.isoformat() if at else None,
        "age_seconds": (now - at).total_seconds() if at else None,
        "freshness_verified": False,  # A response alone is not proof of a usable live feed.
    }


def _symbols_from_extractions(cfg: Config) -> list[str]:
    from crown.letter.inventory import load_all

    records, _ = load_all(cfg)
    syms: set[str] = set()
    for r in records:
        for p in r.extraction.crown_positions:
            syms.add(p.instrument.primary.upper())
            syms.update(a.upper() for a in p.instrument.alternatives)
    return sorted(s for s in syms if s.isalpha() and len(s) <= 5)


def run_probe(
    cfg: Config, letter_period_start: date | None = None, *,
    trading=None, stocks=None, options=None, news=None,
) -> dict:
    """GET-only paper discovery. Inject clients for offline tests; never change capabilities."""
    if not alpaca_is_paper():
        raise RuntimeError("Phase 0 probe is paper-only. Set ALPACA_PAPER=true.")
    if any(client is None for client in (trading, stocks, options, news)):
        key, secret = alpaca_credentials()
        # Raw account data tolerates removed SDK fields. Project only known non-ID fields.
        trading = trading if trading is not None else TradingClient(key, secret, paper=True, raw_data=True)
        stocks = stocks if stocks is not None else StockHistoricalDataClient(key, secret)
        options = options if options is not None else OptionHistoricalDataClient(key, secret)
        news = news if news is not None else NewsClient(key, secret)
    start = letter_period_start or date.fromisoformat(cfg.backtest.start_date)

    report: dict = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "account_probed": "paper",
        "operating_policy": {"cash_only": True, "no_shorting": True},
        "legal_account_type_verified": False,
        "settlement": {
            "verified": False,
            "settled_cash": None,
            "note": "Cash and buying power are observations, not verified settled cash.",
        },
        "order_acceptance_verified": False,
    }

    # --- account -----------------------------------------------------------
    acct = _safe(trading.get_account)
    if acct["ok"]:
        a = acct["value"]
        report["account"] = {name: _field(a, name) for name in (
            "status", "multiplier", "shorting_enabled", "options_approved_level",
            "options_trading_level", "cash", "buying_power", "non_marginable_buying_power",
            "options_buying_power", "equity", "trading_blocked", "account_blocked",
            "trade_suspended_by_user",
        )}
    else:
        report["account"] = acct
        report["blockers"] = ["Paper account could not be read; remaining API checks were not run."]
        return report

    cfgs = _safe(trading.get_account_configurations)
    report["account_configurations"] = ({name: _field(cfgs["value"], name) for name in (
        "fractional_trading", "max_margin_multiplier", "no_shorting", "suspend_trade",
        "max_options_trading_level",
    )} if cfgs["ok"] else cfgs)
    clock = _safe(trading.get_clock)
    report["market_clock"] = ({name: _field(clock["value"], name) for name in (
        "is_open", "timestamp", "next_open", "next_close",
    )} if clock["ok"] else clock)

    # --- assets: tradable / fractionable / shortable ------------------------
    universe = sorted(set(DEFAULT_UNIVERSE) | set(_symbols_from_extractions(cfg)))
    assets = {}
    for sym in universe:
        res = _safe(lambda s=sym: trading.get_asset(s))
        if res["ok"]:
            x = res["value"]
            assets[sym] = {
                "tradable": _field(x, "tradable"), "fractionable": _field(x, "fractionable"),
                "shortable": _field(x, "shortable"), "status": _field(x, "status"),
                "exchange": _field(x, "exchange"), "attributes": _field(x, "attributes"),
            }
        else:
            assets[sym] = {**res, "tradable": None}
    report["assets"] = assets

    # --- options: listing + historical bars over the letter period ----------
    opt: dict = {}
    for und in OPTION_TEST_UNDERLYINGS:
        entry: dict = {}
        active = _safe(lambda u=und: trading.get_option_contracts(
            GetOptionContractsRequest(underlying_symbols=[u], limit=5)))
        entry["active_contracts_listed"] = (
            len(_field(active["value"], "option_contracts", []) or []) if active["ok"] else active
        )
        expired = _safe(lambda u=und: trading.get_option_contracts(GetOptionContractsRequest(
            underlying_symbols=[u], status=AssetStatus.INACTIVE,
            expiration_date_gte=start + timedelta(days=14),
            expiration_date_lte=start + timedelta(days=45), limit=5)))
        contracts = _field(expired.get("value"), "option_contracts", []) or []
        if expired["ok"] and contracts:
            c = contracts[0]
            symbol = _field(c, "symbol")
            expiry = _field(c, "expiration_date")
            expiry = expiry if isinstance(expiry, date) else date.fromisoformat(expiry)
            bars = _safe(lambda s=symbol: options.get_option_bars(OptionBarsRequest(
                symbol_or_symbols=s, timeframe=TimeFrame.Day,
                start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
                end=datetime.combine(expiry + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
            )))
            entry["historical_contract"] = symbol
            entry["historical_daily_bars"] = (
                len(_field(bars["value"], "data", {}).get(symbol, [])) if bars["ok"] else bars
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
        feeds[feed.value] = _quote_snapshot(q["value"], "SPY", datetime.now(timezone.utc)) if q["ok"] else q
    hist = _safe(lambda: stocks.get_stock_bars(StockBarsRequest(
        symbol_or_symbols="SPY", timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(start + timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc),
    )))
    feeds["historical_daily_bars_letter_period"] = (
        len(hist["value"].data.get("SPY", [])) if hist["ok"] else hist
    )
    report["market_data"] = feeds

    # Access/freshness metadata only; do not archive article content in an account probe.
    recent = _safe(lambda: news.get_news(NewsRequest(symbols="SPY", limit=1, include_content=False)))
    if recent["ok"]:
        articles = _field(recent["value"], "data", {}).get("news", [])
        report["news"] = {
            "ok": True, "articles_returned": len(articles),
            "latest_published_at": _field(articles[0], "created_at") if articles else None,
            "latest_updated_at": _field(articles[0], "updated_at") if articles else None,
            "freshness_verified": False,
        }
    else:
        report["news"] = recent
    return report


def summarize(report: dict) -> list[str]:
    out = [f"Account probed: {report['account_probed'].upper()}"]
    a = report.get("account", {})
    if "multiplier" in a:
        out += [
            f"  observed multiplier={a['multiplier']}; shorting_enabled={a['shorting_enabled']}",
            f"  options approved/trading level: {a['options_approved_level']}/{a['options_trading_level']}",
            f"  cash={a['cash']} buying_power={a['buying_power']} non_marginable_bp={a['non_marginable_buying_power']}",
        ]
    else:
        out.append(f"  account unavailable: {error_label(a)}")
    assets = report.get("assets", {})
    frac = sorted(s for s, v in assets.items() if v.get("fractionable"))
    nonfrac = sorted(s for s, v in assets.items() if v.get("tradable") and not v.get("fractionable"))
    untradable = sorted(s for s, v in assets.items() if v.get("tradable") is False)
    unknown = sorted(s for s, v in assets.items() if v.get("tradable") is None)
    out += [f"  fractionable ({len(frac)}): {' '.join(frac)}",
            f"  tradable, NOT fractionable ({len(nonfrac)}): {' '.join(nonfrac)}",
            f"  not tradable ({len(untradable)}): {' '.join(untradable)}",
            f"  asset lookup unavailable ({len(unknown)}): {' '.join(unknown)}"]
    for und, o in report.get("options", {}).items():
        out.append(f"  options {und}: active listed={o.get('active_contracts_listed')}, "
                   f"historical {o.get('historical_contract')} bars={o.get('historical_daily_bars', o.get('historical_lookup'))}")
    md = report.get("market_data", {})
    out.append(f"  data feeds: iex={'ok' if md.get('iex', {}).get('ok') else md.get('iex')}, "
               f"sip={'ok' if md.get('sip', {}).get('ok') else 'unavailable'}; "
               f"SPY daily bars in letter period={md.get('historical_daily_bars_letter_period')}")
    n = report.get("news", {})
    out.append(f"  news: {'accessible' if n.get('ok') else error_label(n)}; "
               f"articles={n.get('articles_returned', 'unknown')}")
    out.append("  NOT verified: legal account type, settled cash, order acceptance, data freshness.")
    if report["account_probed"] == "paper":
        out.append("  Paper observations do not verify live settings; cash-only policy is unchanged.")
    return out


def write_probe(cfg: Config, report: dict) -> Path:
    cfg.paths.probe.mkdir(parents=True, exist_ok=True)
    observed_date = datetime.fromisoformat(report["probed_at"]).date()
    path = cfg.paths.probe / f"probe-{report['account_probed']}-{observed_date}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
