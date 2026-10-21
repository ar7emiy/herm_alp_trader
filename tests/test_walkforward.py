from datetime import date

from crown.data.calendar import trading_days
from crown.research.walkforward import folds, select_training
from crown.research.models import D
from tests.test_research_replay import START, bar, replay


def candidate(name="base", **changes):
    return {"name":name,"closed_episodes":20,"net_pnl":"80","stress_net_pnl":"40",
            "stress_tested":True,"max_drawdown_pct":"3",**changes}


def test_selection_requires_completed_sample_positive_cost_stress_and_drawdown():
    assert select_training([candidate(closed_episodes=2)]) is None
    assert select_training([candidate(stress_net_pnl="-1")]) is None
    assert select_training([candidate(stress_tested=False)]) is None
    assert select_training([candidate(max_drawdown_pct="7")]) is None
    assert select_training([candidate(),candidate("lower_dd",max_drawdown_pct="2")]) == "lower_dd"


def test_fold_windows_are_chronological_disjoint_with_five_session_gap():
    windows = folds(date(2026,6,1),date(2026,10,16))
    assert len(windows) == 3
    for train_start,train_end,test_start,test_end in windows:
        assert train_start < train_end < test_start <= test_end
        assert len(trading_days(train_end,test_start))-2 == 5
    assert all(a[3] < b[2] for a,b in zip(windows,windows[1:]))


def test_future_price_perturbation_changes_later_pnl_but_not_earlier_events():
    first = bar(START,100,102,98,101)
    low = replay([first,bar(date(2026,6,2),101,102,90,91)])
    high = replay([first,bar(date(2026,6,2),101,110,100,109)])
    assert [e for e in low["events"] if e["at"][:10]==str(START)] == [e for e in high["events"] if e["at"][:10]==str(START)]
    assert D(low["net_pnl"]) != D(high["net_pnl"])
