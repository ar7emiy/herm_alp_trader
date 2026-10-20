from datetime import date
import json

import pytest
from click.testing import CliRunner

from crown.cli import cli
from crown.research.app import backtest
from crown.research.models import ResearchPolicy
from crown.research.report import write_report
from tests.test_research_replay import START,bar,replay


def test_runtime_output_cannot_escape_private_reports(cfg,tmp_path):
    with pytest.raises(RuntimeError,match="private reports"):
        backtest(cfg,date(2026,6,1),date(2026,6,2),out=tmp_path/"public-export")


def test_default_backtest_is_v3_and_offline_never_loads_env(monkeypatch,tmp_path):
    seen=[]
    def config(**kwargs):
        assert kwargs=={"load_env":False}
        return "safe-config"
    def run(cfg,start,end,policy_name,offline,out):
        assert cfg=="safe-config" and offline and policy_name=="both"
        seen.append((start,end))
        return tmp_path/"index.html",[replay([bar(START)])]
    monkeypatch.setattr("crown.cli.load_config",config)
    monkeypatch.setattr("crown.research.app.backtest",run)
    result=CliRunner().invoke(cli,["backtest","--from","2026-06-01","--to","2026-06-02","--offline"])
    assert result.exit_code==0,result.output
    assert seen and "no broker orders" in result.output


def test_report_is_self_contained_and_source_text_cannot_break_script(tmp_path):
    r=replay([bar(START)])
    manifest={"sources":[{"quote":"</script><script>alert(1)</script>"}]}
    path=write_report(tmp_path/"report",manifest,[r])
    html=path.read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html
    assert 'src="http' not in html and 'href="http' not in html
    assert len(json.loads(path.with_name("results.json").read_text())["results"])==1
