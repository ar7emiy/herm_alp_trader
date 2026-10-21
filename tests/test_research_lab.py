from dataclasses import replace
from datetime import date, timedelta
import json

import pytest
from click.testing import CliRunner

from crown.cli import cli
from crown.config import alpaca_credentials, alpaca_is_paper
from crown.research.app import policy_from_config
from crown.research.lab import experiments, laboratory, metrics, promotion_assessment, write_lab_report
from crown.research.models import D, digest
from crown.research.runner import run
from tests.test_research_replay import START, bar, replay
from tests.test_research_compiler import source


@pytest.mark.parametrize("mode", ["false", "0", "no", "live", "", "yes", "tru"])
def test_live_or_ambiguous_mode_rejected_before_credentials(monkeypatch, mode):
    monkeypatch.setenv("ALPACA_PAPER", mode)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="paper-only"):
        alpaca_credentials()
    with pytest.raises(RuntimeError, match="paper-only"):
        alpaca_is_paper()


def test_paper_default_and_explicit_true(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    assert alpaca_is_paper() is True
    monkeypatch.setenv("ALPACA_PAPER", "true")
    assert alpaca_is_paper() is True


def test_percentage_caps_follow_capital_but_dollar_limits_remain_explicit(cfg):
    large = replace(cfg, sizing=replace(cfg.sizing, sleeve_override=100000))
    policy = policy_from_config(large)
    assert policy.initial_cash == 100000 and policy.risk_unit == 1500
    assert policy.max_open_risk == 6000 and policy.max_thesis_risk == 2250
    assert policy.max_cluster_risk == 3000 and policy.weekly_loss_limit == 6000
    assert policy.max_order_notional == cfg.guardrails.max_order_notional
    assert policy.daily_loss_limit == cfg.guardrails.daily_loss_limit


def test_bounded_ablation_set_cannot_multiply_a_budget(cfg):
    recipes = experiments(cfg)
    assert len(recipes) == len({r.policy.name for r in recipes}) == 9
    assert all(r.policy.initial_cash == 5000 for r in recipes)
    assert max(r.policy.risk_unit for r in recipes) == 75
    assert all(r.policy.max_open_risk == 300 for r in recipes)
    with pytest.raises(RuntimeError, match="5,000"):
        experiments(replace(cfg, sizing=replace(cfg.sizing, sleeve_override=100000)))


def test_results_respond_to_prices_and_do_not_share_cash_or_halt():
    down = replay([bar(START, 100, 101, 90, 91)])
    up = replay([bar(START, 100, 110, 99, 109)])
    again = replay([bar(START, 100, 101, 90, 91)])
    assert D(down["net_pnl"]) < 0 < D(up["net_pnl"])
    assert down == again
    assert down["initial_equity"] == up["initial_equity"] == "5000"
    assert metrics(down)["closed_episodes"] == 1
    assert D(metrics(down)["modeled_fees"]) > 0


def test_all_historical_outcomes_remain_exploratory_even_if_positive():
    result = promotion_assessment([{"closed_episodes": 1000, "net_pnl": "10000"}])
    assert result["selected_strategy"] is None and result["promotion_allowed"] is False


def test_lab_registers_before_replay_writes_nine_isolated_journals(cfg, monkeypatch):
    import crown.research.lab as lab
    out = cfg.paths.reports/"lab-test"
    monkeypatch.setattr(lab, "load_sources", lambda cfg: ([source()], [{"pdf": "synthetic"}]))
    days = [date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)]
    # XYZ entry 20/stop 19 from the synthetic source; tests exercise real compiler/gateway.
    bars = {"XYZ": {d: bar(d, 20, 21, 19.5, 20, "XYZ") for d in days}}
    monkeypatch.setattr(lab, "load_bars", lambda cfg, symbols, start, end, offline: (bars, []))
    def registered(*args, **kwargs):
        m = json.loads((out/"registration.json").read_text())
        assert len(m["experiments"]) == 9 and m["orders_enabled"] is False
        assert args[5] == digest(m)
        return run(*args, **kwargs)
    monkeypatch.setattr(lab, "run", registered)
    path, study = laboratory(cfg, START, days[-1], out)
    assert path.exists() and len(study["summaries"]) == 9
    assert len(list(out.glob("*.sqlite"))) == 9
    assert all(s["net_pnl"] is not None for s in study["summaries"])
    assert json.loads((out/"completion.json").read_text())["status"] == "complete"
    assert study["assessment"]["promotion_allowed"] is False
    with pytest.raises(RuntimeError, match="never overwritten"):
        laboratory(cfg, START, days[-1], out)


def test_failed_experiments_leave_registration_and_failure_evidence(cfg, monkeypatch):
    import crown.research.lab as lab
    monkeypatch.setattr(lab, "load_sources", lambda cfg: ([source()], []))
    monkeypatch.setattr(lab, "load_bars", lambda *a, **k: ({}, []))
    def fail(*a, **k):
        raise RuntimeError("sensitive-provider-detail")
    monkeypatch.setattr(lab, "run", fail)
    out = cfg.paths.reports/"failed"
    with pytest.raises(RuntimeError):
        laboratory(cfg, START, date(2026, 6, 3), out)
    assert (out/"registration.json").exists()
    text = (out/"completion.json").read_text()
    assert '"status": "failed"' in text and "sensitive-provider-detail" not in text


def test_lab_cannot_export_private_material_outside_reports(cfg, tmp_path):
    with pytest.raises(RuntimeError, match="private reports"):
        laboratory(cfg, START, START+timedelta(days=1), tmp_path/"public")


def test_lab_cli_always_offline(monkeypatch, tmp_path):
    def config(**kwargs):
        assert kwargs == {"load_env": False}
        return "offline"
    monkeypatch.setattr("crown.cli.load_config", config)
    monkeypatch.setattr("crown.research.lab.laboratory", lambda *a: (tmp_path/"index.html", {"summaries": []}))
    result = CliRunner().invoke(cli, ["lab", "--from", "2026-06-01", "--to", "2026-06-03"])
    assert result.exit_code == 0 and "no live mode" in result.output


def test_private_dossier_cannot_inject_executable_html(tmp_path):
    manifest = {"start":"2026-06-01","end":"2026-10-16","split_day":"2026-08-01","experiments":[]}
    study = {"summaries":[],"assessment":{"reasons":[]},"walkforward":{"folds":[]},
             "coverage":{"blocker_counts":{},"rows":[{"quote":"</script><script>alert(1)</script>"}]}}
    write_lab_report(tmp_path,manifest,study)
    html = (tmp_path/"index.html").read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html
    assert 'src="http' not in html and 'href="http' not in html
