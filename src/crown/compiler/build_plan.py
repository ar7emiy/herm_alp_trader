"""ParsedLetter -> ContingentPlan. Pure Python, no LLM, no market data.

This is the module that turns a Monday's reasoning into a deterministic
artifact the backtest/live executor can replay with zero further judgment
calls. Implements 06-sizing-and-alternatives.md (sizing math, thesis
classification, Alternative Finder) and the structural parts of the no-chase
rule from the same doc (the live price comparison itself happens later, in
engine/zonecheck.py, which is the only place that touches market data).

v1 data-availability limitations (all produce an explicit skip_reason, never
a silent guess):
  - "pair" and "seagull" trade types are not modeled — a pair spread needs
    explicit leg ratios the letter doesn't give, and a seagull needs options
    chain data we don't have historically.
  - Options-structure TACTICAL rows (not featured) with neither an explicit
    entry_conditions band nor legs are skipped: comparing a spread credit
    against underlying-price bars isn't meaningful, and we have no historical
    options chain to check a live credit against.
  - The Alternative Finder's "narrow the spread width" and "single
    defined-risk leg" steps (06 Steps 1-2) require live options chain data
    we don't have. v1 only implements Step 4 (equity/ETF proxy) before
    falling to Step 5 (skip).
  - FX and futures-root instruments have no historical price source in v1
    (Alpaca serves US equities/ETFs) and are always skipped, matching the
    regression requirement that an untriggered FX conditional is never
    staged regardless.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from crown.config import Config, load_config
from crown.models import (
    ContingentPlan,
    EntryZone,
    FeaturedTrade,
    HouseViewRow,
    OptionLeg,
    ParsedLetter,
    PlannedSize,
    PlannedTarget,
    PlannedTrade,
    StopState,
    TacticalSummaryRow,
    WatchZone,
)

# Known non-equity roots: FX majors and common futures roots Crown quotes
# alongside their ETF proxy (e.g. "HG / CPER"). None of these have a
# historical price source in v1.
_FX_PAIRS = {
    "EURUSD", "USDJPY", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURJPY", "EURGBP", "USDCNH", "USDMXN",
}
_FUTURES_ROOTS = {"HG", "CL", "GC", "SI", "ZC", "ZS", "ZW", "NG", "HO", "RB", "SI", "ZB"}

_REGIME_KEYWORDS = ("zero-gamma", "zero gamma", "put wall", "call wall", "gamma flip")

_DOLLAR_RANGE_RE = re.compile(r"\$?\s*(\d{2,6}(?:\.\d+)?)\s*(?:[-–]\s*\$?\s*(\d{2,6}(?:\.\d+)?))?")
_SESSION_COUNT_RE = re.compile(r"(\d+)\s+session", re.IGNORECASE)


def _extract_session_count(text: str) -> int:
    """'close at 50% of max loss if X sustains above Y for 2 sessions'
    (06-sizing-and-alternatives.md) -> 2. Defaults to 1 (evaluate at the very
    next close) when the rule doesn't name a session count — most behavioral
    rules in practice are simple single-close breaches, not multi-day
    sustains, and requiring 1 session is the conservative default (never
    silently waits longer than the letter actually asked for)."""
    match = _SESSION_COUNT_RE.search(text)
    return int(match.group(1)) if match else 1


def _extract_level_from_text(text: str) -> float | None:
    """Best-effort numeric level extraction from a behavioral stop rule like
    'reclaim of $391-392 zone'. Returns the midpoint of a range, or the
    single value found, or None if nothing parseable — a None result means
    the zone-check can log the rule but cannot mechanically evaluate it."""
    match = _DOLLAR_RANGE_RE.search(text)
    if not match:
        return None
    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) else None
    return (low + high) / 2 if high is not None else low


def _resolve_symbol(
    primary_text: str,
    instrument_alternatives: list[str],
) -> tuple[str | None, str, str | None]:
    """Returns (traded_symbol, asset_class, note)."""

    def classify(sym: str) -> str:
        s = sym.upper()
        if s in _FX_PAIRS or s in _FUTURES_ROOTS:
            return "fx_or_futures_no_data"
        return "equity"

    candidates = list(instrument_alternatives)
    if not candidates:
        m = re.match(r"^\(?([A-Z]{1,6})\b", primary_text.strip())
        if m:
            candidates = [m.group(1)]

    if not candidates:
        return None, "fx_or_futures_no_data", f"could not extract a ticker from '{primary_text}'"

    tradable = [c for c in candidates if classify(c) != "fx_or_futures_no_data"]
    if tradable:
        chosen = tradable[0]
        note = None
        if len(candidates) > 1:
            note = f"resolved '{primary_text}' -> {chosen} from alternatives {candidates}"
        return chosen, "equity", note

    return None, "fx_or_futures_no_data", (
        f"no tradable equity/ETF leg among {candidates or [primary_text]}; "
        "no historical price source for FX/futures roots in v1"
    )


def _direction_for(view: str, trade_type: str) -> str | None:
    if view == "bullish":
        return "long"
    if view in ("bearish", "breakdown_hedge"):
        return "short"
    return None  # pair / ambiguous


def _make_stop(stop_type: str, value: float | None, rule: str | None) -> StopState:
    """`current`/`initial` hold a PRICE level only — comparable directly to
    bars by the zone-check engine. A dollar_loss stop's `value` is a $
    threshold, not a price, so it is deliberately NOT stored here (it lives
    on PlannedTrade.max_risk_per_unit instead) — storing it here would make
    _watch_zones_for_trade create a nonsense price-comparison zone."""
    current: float | None = None
    sessions_required = None
    if stop_type == "price":
        current = value
    elif stop_type == "behavioral" and rule:
        current = _extract_level_from_text(rule)
        if current is not None:
            sessions_required = _extract_session_count(rule)
    return StopState(
        type=stop_type,
        initial=current,
        current=current,
        rule=rule,
        behavioral_sessions_required=sessions_required,
    )


def _house_view_conflict_note(direction: str | None, symbol: str | None, house_view: list[HouseViewRow]) -> str | None:
    """Informational only — doc 04 is explicit that a tactical trade
    conflicting with the house view is NOT grounds to veto. We only ever
    attach a note here, never change `action`."""
    if not direction or not house_view:
        return None
    for row in house_view:
        view_lower = row.view.lower()
        bullish_house = "bullish" in view_lower or "constructive" in view_lower
        bearish_house = "bearish" in view_lower
        if direction == "short" and bullish_house:
            return f"tactical short conflicts with house view on {row.asset_class} ({row.view}) — not a veto, noted only"
        if direction == "long" and bearish_house:
            return f"tactical long conflicts with house view on {row.asset_class} ({row.view}) — not a veto, noted only"
    return None


def _max_risk_per_contract(
    trade_type: str,
    explicit_max_risk: float | None,
    entry_level: float | None,
    stop_type: str | None,
    stop_value: float | None,
) -> tuple[float | None, str]:
    if explicit_max_risk is not None:
        return explicit_max_risk, "crown_exact (letter-stated max_risk)"
    if trade_type == "debit_spread" and entry_level is not None:
        return entry_level * 100, "crown_exact (debit spread: max loss = debit paid)"
    if stop_type == "dollar_loss" and stop_value is not None:
        return stop_value, "crown_exact (letter's dollar-loss stop read as max risk/contract)"
    return None, "no max-risk data available for this options structure"


def _multiplier_or_default(m: float | None, cfg: Config) -> tuple[float, str | None]:
    """06-sizing-and-alternatives.md: never infer size from conviction, never
    fabricate. A missing sizing_multiplier is a real letter-parsing gap, not
    something to silently zero out — we use the smallest standard tier as a
    conservative, clearly-logged default rather than dropping the trade."""
    if m is not None:
        return m, None
    default = cfg.alternative_finder.missing_multiplier_default
    return default, (
        f"letter gave no sizing_multiplier for this row; defaulted to {default} "
        "(smallest standard tier) — flag for human review before trusting this size"
    )


def _size(risk_budget: float, distance_or_max_risk: float | None, is_options: bool) -> PlannedSize:
    if distance_or_max_risk is None or distance_or_max_risk <= 0:
        return PlannedSize(risk_dollars=risk_budget, fits=False, method="skipped",
                            notes=["no valid stop distance / max-risk to size against"])
    if is_options:
        contracts = int(risk_budget // distance_or_max_risk)
        return PlannedSize(
            contracts=contracts, risk_dollars=risk_budget,
            fits=contracts >= 1, method="crown_exact",
        )
    shares = int(risk_budget // distance_or_max_risk)
    return PlannedSize(
        shares=shares, risk_dollars=risk_budget,
        fits=shares >= 1, method="crown_exact",
    )


def _plan_featured_trade(ft: FeaturedTrade, cfg: Config, letter_date: str, idx: int) -> PlannedTrade:
    trade_id = f"{letter_date}:featured:{idx}:{ft.symbol}"
    log: list[str] = []

    symbol, asset_class, note = _resolve_symbol(ft.symbol, [])
    # Featured trades (condors, spreads) are usually multi-leg with no single
    # directional view field. "long" is a placeholder direction used only to
    # satisfy the schema for the crown_exact options path (the stop ratchet
    # doesn't apply to a non-directional structure); it's overridden with a
    # real direction only if the Alternative Finder falls back to an
    # equity-proxy long position below.

    multiplier, mult_note = _multiplier_or_default(ft.sizing_multiplier, cfg)
    sleeve_budget = cfg.sizing.risk_at_1_00 * multiplier
    if mult_note:
        log.append(mult_note)

    if asset_class == "fx_or_futures_no_data":
        return PlannedTrade(
            trade_id=trade_id, source="featured", crown_symbol=ft.symbol,
            asset_class=asset_class, direction="long", structure="other",
            entry_zone=EntryZone(trigger_type="conditional_level"),
            entry_window_open=letter_date,
            stop=StopState(type="behavioral"),
            size=PlannedSize(risk_dollars=0, fits=False, method="skipped"),
            action="skipped", skip_reason=note,
        )

    if not ft.legs:
        return PlannedTrade(
            trade_id=trade_id, source="featured", crown_symbol=ft.symbol,
            traded_symbol=symbol, asset_class=asset_class, direction="long",
            structure="other",
            entry_zone=EntryZone(trigger_type="conditional_level"),
            entry_window_open=letter_date,
            stop=StopState(type="behavioral"),
            size=PlannedSize(risk_dollars=sleeve_budget, fits=False, method="skipped"),
            action="skipped",
            skip_reason="featured trade has no leg structure given; cannot size or settle",
        )

    max_risk, method_note = _max_risk_per_contract(
        "iron_condor", ft.max_risk, ft.target_credit, None, None,
    )
    log.append(method_note)
    size = _size(sleeve_budget, max_risk, is_options=True)

    alt_used = "crown_exact"
    if not size.fits:
        log.append(
            f"Crown's exact structure needs {sleeve_budget:.2f} budget / "
            f"{max_risk} max_risk -> {size.contracts} contracts; doesn't fit sleeve"
        )
        log.append("Alt Finder Step 1 (narrow width): not attempted — no options chain data in v1")
        log.append("Alt Finder Step 2 (single leg): not attempted — no options chain data in v1")
        proxy_symbol, proxy_asset, proxy_note = _resolve_symbol(ft.symbol, [])
        if proxy_asset != "fx_or_futures_no_data" and ft.entry_conditions and ft.entry_conditions.underlying_high:
            band = ft.entry_conditions
            width = (band.underlying_high or 0) - (band.underlying_low or 0)
            if width > 0:
                shares = int(sleeve_budget // width) if width else 0
                size = PlannedSize(shares=shares, risk_dollars=sleeve_budget,
                                    fits=shares >= 1, method="equity_proxy",
                                    notes=["Alt Finder Step 4: sized as equity/ETF exposure "
                                           "using the entry band width as a stand-in stop distance"])
                alt_used = "equity_proxy"
                log.append(f"Alt Finder Step 4 (equity proxy): sized {shares} shares of {proxy_symbol}")
            else:
                log.append("Alt Finder Step 4: entry band has zero width, cannot use as stop distance")
        if not size.fits:
            log.append("Alt Finder Step 5: SKIP — no viable alternative sizes within the sleeve")

    action = "watchlist" if size.fits else "skipped"
    skip_reason = None if size.fits else "Alternative Finder exhausted; nothing sizes within the sleeve"

    entry_zone = EntryZone(trigger_type="conditional_band")
    if ft.entry_conditions:
        entry_zone.low = ft.entry_conditions.underlying_low
        entry_zone.high = ft.entry_conditions.underlying_high

    legs = ft.legs if alt_used == "crown_exact" else []
    structure = _infer_structure(ft.legs)

    conviction_flag = ft.conviction_flag
    house_note = None  # featured trades don't carry a single directional view; skip conflict check

    targets: list[PlannedTarget] = []  # featured trades don't carry T1/T2/T3 in doc 05 schema
    expiry = ft.expiry or (legs[0].expiry if legs else None)

    return PlannedTrade(
        trade_id=trade_id, source="featured", crown_symbol=ft.symbol,
        traded_symbol=symbol if size.fits else None,
        asset_class="options_spread" if alt_used == "crown_exact" else asset_class,
        direction="long",
        structure=structure,
        legs=legs,
        credit_or_debit=ft.target_credit,
        max_risk_per_unit=max_risk,
        expiry=expiry,
        entry_zone=entry_zone,
        entry_window_open=letter_date,
        entry_window_close=ft.entry_window_close,
        stop=StopState(type="behavioral", rule=ft.invalidation),
        targets=targets,
        size=size,
        conviction=ft.conviction,
        conviction_flag=conviction_flag,
        sizing_multiplier_letter=ft.sizing_multiplier,
        action=action,
        skip_reason=skip_reason,
        house_view_conflict_note=house_note,
        symbol_resolution_note=note,
        alt_finder_log=log,
    )


def _infer_structure(legs: list[OptionLeg]) -> str:
    if len(legs) == 4:
        return "iron_condor"
    if len(legs) == 2:
        calls = [l for l in legs if l.right == "call"]
        return "bear_call" if calls else "bull_put"
    return "other"


def _plan_tactical_row(row: TacticalSummaryRow, cfg: Config, letter_date: str, idx: int,
                        house_view: list[HouseViewRow]) -> PlannedTrade:
    trade_id = f"{letter_date}:tactical:{idx}"
    log: list[str] = []

    if row.trade_type in ("pair", "seagull"):
        return PlannedTrade(
            trade_id=trade_id, source="tactical", crown_symbol=row.market,
            asset_class="fx_or_futures_no_data", direction="long", structure=row.trade_type,
            entry_zone=EntryZone(trigger_type="conditional_level"),
            entry_window_open=letter_date,
            stop=_make_stop(row.stop.type, row.stop.value, row.stop.rule),
            size=PlannedSize(risk_dollars=0, fits=False, method="skipped"),
            action="skipped",
            skip_reason=f"{row.trade_type} structures are not modeled in v1 "
                        "(no leg-ratio / options-chain data available)",
        )

    symbol, asset_class, note = _resolve_symbol(row.market, row.instrument_alternatives)
    direction = _direction_for(row.view, row.trade_type)

    if asset_class == "fx_or_futures_no_data":
        return PlannedTrade(
            trade_id=trade_id, source="tactical", crown_symbol=row.market,
            asset_class=asset_class, direction=direction or "long", structure=row.trade_type,
            entry_zone=EntryZone(trigger_type="conditional_level",
                                  reference_level=row.entry_trigger.level),
            entry_window_open=letter_date,
            entry_window_close=row.entry_window_close,
            stop=_make_stop(row.stop.type, row.stop.value, row.stop.rule),
            targets=[PlannedTarget(tier=t.tier, level=t.level) for t in row.targets if t.level is not None],
            size=PlannedSize(risk_dollars=0, fits=False, method="skipped"),
            action="skipped", skip_reason=note,
            house_view_conflict_note=_house_view_conflict_note(direction, symbol, house_view),
        )

    is_options = row.trade_type in ("bear_call", "bull_put", "iron_condor", "debit_spread")
    multiplier, mult_note = _multiplier_or_default(row.sizing_multiplier, cfg)
    sleeve_budget = cfg.sizing.risk_at_1_00 * multiplier
    if mult_note:
        log.append(mult_note)

    if is_options:
        # Tactical rows carry no legs and no entry_conditions band per the
        # doc 05 schema — only a spread-price entry_trigger.level. Without a
        # historical options chain there's no way to check that credit
        # against a live market, and no underlying band to trigger on.
        max_risk, method_note = _max_risk_per_contract(
            row.trade_type, None, row.entry_trigger.level, row.stop.type, row.stop.value,
        )
        log.append(method_note)
        if max_risk is None:
            return PlannedTrade(
                trade_id=trade_id, source="tactical", crown_symbol=row.market,
                traded_symbol=symbol, asset_class="options_spread", direction=direction or "long",
                structure=row.trade_type,
                entry_zone=EntryZone(trigger_type="conditional_level"),
                entry_window_open=letter_date,
                stop=_make_stop(row.stop.type, row.stop.value, row.stop.rule),
                size=PlannedSize(risk_dollars=sleeve_budget, fits=False, method="skipped"),
                action="skipped",
                skip_reason="tactical options row has no derivable max-risk/contract in v1",
                alt_finder_log=log,
            )
        size = _size(sleeve_budget, max_risk, is_options=True)
        stop_state = _make_stop(row.stop.type, row.stop.value, row.stop.rule)
        behavioral_level_known = row.stop.type == "behavioral" and stop_state.current is not None
        proxy_direction = direction

        if not size.fits:
            log.append(
                f"Crown's exact structure needs {sleeve_budget:.2f} budget / "
                f"{max_risk} max_risk -> {size.contracts} contracts; doesn't fit sleeve"
            )
            log.append("Alt Finder Steps 1-2 (narrow width / single leg): not attempted — "
                        "no options chain data in v1")
            if direction and stop_state.current is not None and stop_state.type in ("behavioral", "price"):
                size = PlannedSize(
                    risk_dollars=sleeve_budget, fits=True, method="equity_proxy_pending_price",
                    notes=[f"Alt Finder Step 4: equity-proxy in {symbol}; share count deferred "
                           "to fill time using the stated invalidation level as the stop "
                           "distance reference (medium confidence — real price-based level)"],
                )
                log.append(size.notes[0])
            elif direction:
                size = PlannedSize(
                    risk_dollars=sleeve_budget, fits=True, method="equity_proxy_pending_price",
                    notes=[f"Alt Finder Step 4: equity-proxy in {symbol}; share count deferred "
                           f"to fill time using a {cfg.alternative_finder.default_proxy_stop_pct:.0%} "
                           "assumed move as the stop distance (LOW confidence — no price-based "
                           "invalidation given for this structure, only a dollar-loss cap)"],
                )
                log.append(size.notes[0])
            else:
                log.append("Alt Finder Step 4: no direction determinable, cannot proxy as equity")

        if not row.entry_trigger.triggered and size.method != "equity_proxy_pending_price":
            # No underlying band to check a conditional options entry against,
            # and no proxy fallback available either.
            action, reason = "skipped", (
                "conditional options entry with no underlying price band to verify "
                "against historical data (tactical rows carry a spread-credit level, "
                "not an underlying trigger)"
            )
        elif not size.fits:
            action, reason = "skipped", "sizes to 0 contracts and no equity-proxy alternative for a tactical options row"
        elif row.stop.type == "behavioral" and not behavioral_level_known and size.method != "equity_proxy_pending_price":
            action, reason = "watchlist", None
            log.append("behavioral stop has no parseable numeric level; will be logged but not auto-closed")
        else:
            action, reason = "watchlist", None

        traded_symbol_final = symbol if action == "watchlist" else None
        asset_class_final = "options_spread" if size.method == "crown_exact" else asset_class
        direction_final = (proxy_direction or "long") if size.method == "equity_proxy_pending_price" else "long"
        # Sign convention for credit_or_debit: positive = cash received
        # (credit structures), negative = cash paid (debit spreads). Only
        # matters if a future extension gives a tactical row real legs for
        # intrinsic-value settlement — today these rows never populate
        # `legs`, so settle_options_at_expiry() can't use this value anyway.
        signed_price = -row.entry_trigger.level if row.trade_type == "debit_spread" and row.entry_trigger.level is not None \
            else row.entry_trigger.level

        entry_zone = EntryZone(
            trigger_type="entered_at_letter",
            reference_level=row.entry_trigger.level,
        )
        return PlannedTrade(
            trade_id=trade_id, source="tactical", crown_symbol=row.market,
            traded_symbol=traded_symbol_final,
            asset_class=asset_class_final, direction=direction_final,
            structure=row.trade_type,
            credit_or_debit=signed_price,
            max_risk_per_unit=max_risk,
            entry_zone=entry_zone,
            entry_window_open=letter_date,
            entry_window_close=row.entry_window_close,
            stop=stop_state,
            targets=[PlannedTarget(tier=t.tier, level=t.level) for t in row.targets if t.level is not None],
            size=size,
            sizing_multiplier_letter=row.sizing_multiplier,
            action=action, skip_reason=reason,
            house_view_conflict_note=_house_view_conflict_note(direction, symbol, house_view),
            symbol_resolution_note=note,
            alt_finder_log=log,
        )

    # long_equity / breakdown_hedge -> plain directional equity/ETF exposure
    stop_state = _make_stop(row.stop.type, row.stop.value, row.stop.rule)
    ref_level = row.entry_trigger.level
    distance = None
    if stop_state.current is not None and ref_level is not None:
        distance = abs(ref_level - stop_state.current)
    size = _size(sleeve_budget, distance, is_options=False)

    if not direction:
        action, reason = "skipped", f"could not determine direction from view='{row.view}'"
    elif not row.entry_trigger.triggered and ref_level is None:
        action, reason = "skipped", "conditional trigger with no numeric level to watch"
    elif not size.fits:
        action, reason = "skipped", "no stop distance available to size a share count, or sizes to 0 shares"
    else:
        action, reason = "watchlist", None

    trigger_type = "entered_at_letter" if row.entry_trigger.triggered else "conditional_level"
    entry_zone = EntryZone(trigger_type=trigger_type, reference_level=ref_level)

    return PlannedTrade(
        trade_id=trade_id, source="tactical", crown_symbol=row.market,
        traded_symbol=symbol if action == "watchlist" else None,
        asset_class=asset_class, direction=direction or "long", structure=row.trade_type,
        entry_zone=entry_zone,
        entry_window_open=letter_date,
        entry_window_close=row.entry_window_close,
        stop=stop_state,
        targets=[PlannedTarget(tier=t.tier, level=t.level) for t in row.targets if t.level is not None],
        size=size,
        sizing_multiplier_letter=row.sizing_multiplier,
        action=action, skip_reason=reason,
        house_view_conflict_note=_house_view_conflict_note(direction, symbol, house_view),
        symbol_resolution_note=note,
        alt_finder_log=log,
    )


def _watch_zones_for_trade(trade: PlannedTrade) -> list[WatchZone]:
    zones: list[WatchZone] = []
    sym = trade.traded_symbol or trade.crown_symbol
    if trade.entry_zone.low is not None:
        zones.append(WatchZone(symbol=sym, kind="entry_low", level=trade.entry_zone.low,
                                meaning="entry band low", linked_trade_id=trade.trade_id))
    if trade.entry_zone.high is not None:
        zones.append(WatchZone(symbol=sym, kind="entry_high", level=trade.entry_zone.high,
                                meaning="entry band high", linked_trade_id=trade.trade_id))
    if trade.entry_zone.trigger_type != "entered_at_letter" and trade.entry_zone.reference_level is not None \
            and trade.entry_zone.low is None:
        zones.append(WatchZone(symbol=sym, kind="entry_low", level=trade.entry_zone.reference_level,
                                meaning="conditional trigger level", linked_trade_id=trade.trade_id))
    if trade.stop.current is not None:
        zones.append(WatchZone(symbol=sym, kind="invalidation", level=trade.stop.current,
                                meaning=trade.stop.rule or f"{trade.stop.type} stop",
                                linked_trade_id=trade.trade_id))
    for t in trade.targets:
        zones.append(WatchZone(symbol=sym, kind="target", level=t.level,
                                meaning=f"{t.tier} target", linked_trade_id=trade.trade_id))
    return zones


def build_plan(letter: ParsedLetter, cfg: Config | None = None) -> ContingentPlan:
    cfg = cfg or load_config()
    trades: list[PlannedTrade] = []

    for i, ft in enumerate(letter.featured_trades):
        trades.append(_plan_featured_trade(ft, cfg, letter.letter_date, i))

    for i, row in enumerate(letter.tactical_summary):
        trades.append(_plan_tactical_row(row, cfg, letter.letter_date, i, letter.house_view))

    watch_zones: list[WatchZone] = []
    for t in trades:
        if t.action == "watchlist":
            watch_zones.extend(_watch_zones_for_trade(t))

    for kl in letter.key_levels:
        if any(kw in kl.meaning.lower() for kw in _REGIME_KEYWORDS):
            watch_zones.append(WatchZone(symbol=kl.symbol, kind="regime", level=kl.level,
                                          meaning=kl.meaning, linked_trade_id=None))

    whitelist = sorted({t.traded_symbol for t in trades if t.traded_symbol})

    return ContingentPlan(
        letter_date=letter.letter_date,
        compiled_at=datetime.now(timezone.utc).isoformat(),
        trades=trades,
        watch_zones=watch_zones,
        ticker_whitelist=whitelist,
        house_view=letter.house_view,
    )
