from datetime import date
from types import SimpleNamespace
import hashlib
import json

import pandas as pd
import pytest

from crown.data import alpaca_bars
from crown.data.calendar import next_funding_day
from crown.research.data import load_bars


def test_iex_raw_is_explicit_and_cached_feed_identity_is_checked(cfg, monkeypatch):
    calls = []

    class Client:
        def get_stock_bars(self, request):
            assert request.feed.value == "iex" and request.adjustment.value == "raw"
            assert request.asof == "-"
            calls.append(request)
            index = pd.MultiIndex.from_tuples([("XYZ", pd.Timestamp("2026-06-01T04:00:00Z"))])
            return SimpleNamespace(df=pd.DataFrame([[100,102,99,101,1000]], index=index, columns=alpaca_bars.BAR_COLUMNS))

    monkeypatch.setattr(alpaca_bars, "alpaca_credentials", lambda: ("synthetic", "synthetic"))
    monkeypatch.setattr(alpaca_bars, "StockHistoricalDataClient", lambda *_: Client())
    start,end=date(2026,6,1),date(2026,6,3)
    alpaca_bars.fetch_daily_bars(["XYZ"],start,end,cfg)
    alpaca_bars.fetch_daily_bars(["XYZ"],start,end,cfg)
    assert len(calls)==1
    meta=next(cfg.paths.bars.glob("*.json"))
    meta.write_text(meta.read_text().replace('"iex"','"sip"'))
    alpaca_bars.fetch_daily_bars(["XYZ"],start,end,cfg)
    assert len(calls)==2


def test_conservative_funding_calendar_handles_market_open_bank_holiday():
    assert next_funding_day(date(2026,10,9))==date(2026,10,13)
    assert next_funding_day(date(2026,6,18))==date(2026,6,22)
    assert next_funding_day(date(2026,7,2))==date(2026,7,6)


def test_offline_replay_rejects_a_tampered_bar_cache_without_loading_keys(cfg,monkeypatch):
    cfg.paths.bars.mkdir()
    path=cfg.paths.bars/"XYZ_iex_raw_daily_2026-04-01_2026-06-30.parquet"
    frame=pd.DataFrame([[100,102,99,101,1000]],index=pd.DatetimeIndex(["2026-06-01T04:00:00Z"]),columns=alpaca_bars.BAR_COLUMNS)
    frame.to_parquet(path)
    path.with_suffix(".json").write_text(json.dumps(dict(symbol="XYZ",feed="iex",adjustment="raw",asof="-",timeframe="1Day",
        start="2026-04-01",end_exclusive="2026-06-30",queried_at="2026-10-19",sha256=hashlib.sha256(path.read_bytes()).hexdigest())))
    monkeypatch.setattr(alpaca_bars,"alpaca_credentials",lambda:pytest.fail("offline credential access"))
    bars,_=load_bars(cfg,{"XYZ"},date(2026,6,1),date(2026,6,2),offline=True)
    assert bars["XYZ"][date(2026,6,1)].close==101
    path.write_bytes(path.read_bytes()+b"tampered")
    with pytest.raises(RuntimeError,match="hash-validated"):
        load_bars(cfg,{"XYZ"},date(2026,6,1),date(2026,6,2),offline=True)
