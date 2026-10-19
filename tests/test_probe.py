from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from crown import probe
from crown.cli import cli
from crown.config import load_config


class PaperAccount:
    """Only GET methods exist: mutation would fail the probe scenarios."""
    def get_account(self):
        return {
            "status": "ACTIVE", "multiplier": "1", "shorting_enabled": False,
            "options_approved_level": 2, "options_trading_level": 2,
            "cash": "5000", "equity": "5000", "buying_power": "5000",
            "account_number": "private-account-number", "id": "private-account-id",
            # No retired PDT fields and no legal cash-account type assertion.
        }

    def get_account_configurations(self):
        return {"fractional_trading": True, "no_shorting": True,
                "max_margin_multiplier": "1", "private_extra": "must-not-archive"}

    def get_clock(self):
        return {"is_open": True, "timestamp": datetime.now(timezone.utc)}

    def get_asset(self, symbol):
        return {"tradable": True, "fractionable": symbol == "SPY", "shortable": False,
                "status": "active", "exchange": "ARCA", "attributes": []}

    def get_option_contracts(self, request):
        return {"option_contracts": []}


class StockData:
    def get_stock_latest_quote(self, request):
        return {"SPY": SimpleNamespace(timestamp=datetime.now(timezone.utc),
                                       bid_price=100, ask_price=101)}

    def get_stock_bars(self, request):
        return SimpleNamespace(data={"SPY": [object(), object()]})


class NewsData:
    def get_news(self, request):
        assert request.include_content is False and request.limit == 1
        return SimpleNamespace(data={"news": [SimpleNamespace(
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            content="private-article-body", headline="private-headline",
        )]})


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "true")
    return dict(trading=PaperAccount(), stocks=StockData(), options=object(), news=NewsData())


def test_probe_missing_retired_fields_never_certifies_cash_or_order_support(cfg, clients):
    report = probe.run_probe(cfg, **clients)
    assert report["account_probed"] == "paper"
    assert report["account"]["multiplier"] == "1"
    assert report["legal_account_type_verified"] is False
    assert report["settlement"]["settled_cash"] is None
    assert report["settlement"]["verified"] is False
    assert report["order_acceptance_verified"] is False
    assert report["news"]["ok"] is True
    assert report["market_data"]["iex"]["age_seconds"] is not None
    encoded = json.dumps(report, default=str)
    for private in ("private-account", "must-not-archive", "private-article", "private-headline"):
        assert private not in encoded
    summary = "\n".join(probe.summarize(report))
    assert "cash-like" not in summary and "PDT=" not in summary
    assert "NOT verified" in summary
    assert cfg.capabilities.verified is False


def test_probe_refuses_live_before_reading_credentials(cfg, clients, monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")

    def forbidden():
        raise AssertionError("must not read credentials")

    monkeypatch.setattr(probe, "alpaca_credentials", forbidden)
    with pytest.raises(RuntimeError, match="paper-only"):
        probe.run_probe(cfg)


def test_failed_account_stops_discovery_without_leaking_exception(cfg, clients):
    class Rejected(PaperAccount):
        def get_account(self):
            class AuthenticationError(Exception):
                status_code = 401
            raise AuthenticationError("secret-must-not-appear")

        def get_account_configurations(self):
            raise AssertionError("stop after account failure")

    clients["trading"] = Rejected()
    report = probe.run_probe(cfg, **clients)
    assert report["account"] == {"ok": False, "error_type": "AuthenticationError", "http_status": 401}
    assert "assets" not in report and "news" not in report
    assert "secret-must-not-appear" not in json.dumps(report)


def test_quote_snapshots_record_staleness_without_claiming_freshness():
    now = datetime.now(timezone.utc)
    old = SimpleNamespace(timestamp=now - timedelta(days=2), bid_price=100, ask_price=101)
    result = probe._quote_snapshot({"SPY": old}, "SPY", now)
    assert result["age_seconds"] == 172800
    assert result["freshness_verified"] is False
    assert probe._quote_snapshot({}, "SPY", now)["ok"] is False
    old.timestamp = datetime(2026, 1, 1)  # Unknown timezone cannot establish freshness.
    assert probe._quote_snapshot({"SPY": old}, "SPY", now)["timestamp"] is None


def test_report_uses_observation_date_and_private_output_path(cfg, clients):
    report = probe.run_probe(cfg, **clients)
    report["probed_at"] = "2026-10-19T23:59:00+00:00"
    path = probe.write_probe(cfg, report)
    assert path.parent == cfg.paths.probe
    assert path.name == "probe-paper-2026-10-19.json"


def test_config_can_be_loaded_without_accessing_env():
    assert load_config(load_env=False).sizing.sleeve == 5000


def test_openai_check_does_not_print_provider_exception_or_key(cfg, monkeypatch):
    import openai
    import crown.cli as cli_module

    secret = "sensitive-example-token"

    def rejected():
        raise RuntimeError(f"Rejected API key: {secret}")

    monkeypatch.setattr(cli_module, "load_config", lambda: cfg)
    monkeypatch.setattr("crown.config.openai_api_key", lambda: secret)
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(
        models=SimpleNamespace(list=rejected)))
    result = CliRunner().invoke(cli, ["llm-check"])
    assert result.exit_code != 0
    assert "OpenAI model access failed: RuntimeError" in result.output
    assert secret not in result.output
