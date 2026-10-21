import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crown.data.evidence import Observation, ThresholdRule, evaluate, positioning_percentile
from crown.data.robinhood import ResearchTools
from crown.research.models import digest


TUESDAY = datetime(2026, 6, 2, 20, tzinfo=timezone.utc)
FRIDAY = TUESDAY+timedelta(days=3)


def observation(**changes):
    return replace(Observation("COT_TEST_NET_OI", "fraction", TUESDAY, FRIDAY, FRIDAY,
                               Decimal('0.20'), "synthetic", "https://example.test/data", "a"*64), **changes)


def rule(**changes):
    return replace(ThresholdRule("COT_TEST_NET_OI", "fraction", "above", Decimal('0.30'),
                                 timedelta(days=8), timedelta(days=12), {"page": 1, "quote": "test threshold"}), **changes)


def test_tuesday_measurement_is_not_visible_before_friday_publication():
    assert evaluate(rule(), [observation()], TUESDAY)["state"] == "unknown"
    assert evaluate(rule(), [observation()], FRIDAY)["state"] == "not_breached"


def test_revision_and_late_receipt_cannot_leak_into_prior_decisions():
    revised = observation(value=Decimal('.5'), published_at=FRIDAY+timedelta(days=7),
                          received_at=FRIDAY+timedelta(days=8))
    assert evaluate(rule(), [observation(), revised], FRIDAY)["state"] == "not_breached"
    assert evaluate(rule(), [observation(), revised], FRIDAY+timedelta(days=8))["state"] == "invalidated"
    assert evaluate(rule(), [revised], FRIDAY+timedelta(days=7))["state"] == "unknown"


def test_stale_units_or_missing_series_are_unknown_not_healthy():
    assert evaluate(rule(), [observation()], FRIDAY+timedelta(days=20))["state"] == "unknown"
    assert evaluate(rule(units="contracts"), [observation()], FRIDAY)["state"] == "unknown"
    with pytest.raises(ValueError, match="order"):
        observation(received_at=TUESDAY)
    with pytest.raises(ValueError, match="Conflicting"):
        evaluate(rule(), [observation(), observation(value=Decimal('.9'))], FRIDAY)


def test_percentile_uses_prior_values_and_cannot_use_future_revisions():
    rows = [observation(period_at=TUESDAY-timedelta(weeks=i), published_at=FRIDAY-timedelta(weeks=i),
                        received_at=FRIDAY-timedelta(weeks=i), value=Decimal(v)) for i,v in enumerate(['.2','.1','.2','.3'])]
    assert positioning_percentile(rows, "COT_TEST_NET_OI", "fraction", FRIDAY, 3) == Decimal('.5')
    assert positioning_percentile(rows, "COT_TEST_NET_OI", "fraction", FRIDAY, 52) is None


def test_robinhood_mutations_unknown_tools_and_schema_drift_never_dispatch():
    called = []
    async def dispatch(name, args):
        called.append(name)
        return {"ok": True}
    quote = {"name": "get_equity_quotes", "inputSchema": {"type": "object"}}
    mutation = {"name": "place_equity_order"}
    allowed = {t["name"]: digest(t) for t in [quote, mutation]}
    client = ResearchTools([quote, mutation], allowed, dispatch)
    assert client.available == ("get_equity_quotes",)
    for name in ["place_equity_order", "cancel_order", "get_equity_quotes_and_buy", "get_account"]:
        with pytest.raises(RuntimeError, match="allowlisted"):
            asyncio.run(client.call(name, {}))
    assert not called
    assert asyncio.run(client.call("get_equity_quotes", {"symbols": ["SPY"]})) == {"ok": True}
    changed = ResearchTools([quote | {"description": "changed"}], allowed, dispatch)
    with pytest.raises(RuntimeError):
        asyncio.run(changed.call("get_equity_quotes", {}))
