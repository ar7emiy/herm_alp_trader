"""Markdown report for one backtest run. Everything here reads from the
in-memory BacktestResult — the SQLite trade log is the audit trail, not the
report's data source, so the report always reflects exactly what the run
produced."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from crown.backtest.runner import BacktestResult
from crown.config import Config

KNOWN_APPROXIMATIONS = """\
## Known v1 approximations — read before trusting any number above

- **Options early-exit management is heuristic.** "Take profit if it compresses
  fast" style guidance isn't modeled; positions are held until a behavioral/
  price stop is breached (checked once per day, on the close) or expiry.
  Expiry settlement itself IS exact for featured trades with real strikes
  (intrinsic value from the underlying's closing price) — only the *early*
  exit logic is approximate.
- **FX and futures-root instruments are always skipped** — no historical
  price source in v1 (Alpaca serves US equities/ETFs only).
- **"pair" and "seagull" structures are not modeled** — no leg-ratio or
  options-chain data available to construct them faithfully.
- **The Alternative Finder only implements Steps 4 and 5** (equity/ETF proxy,
  then skip) from 06-sizing-and-alternatives.md. Steps 1-2 (narrow the
  spread width, single defined-risk leg) need a historical options chain
  this system doesn't have.
- **Tactical-row options structures without an underlying price reference**
  (only a dollar-loss cap, no behavioral/price level) use a coarse
  {default_proxy_stop_pct:.0%}-of-price assumed stop distance when proxied as
  equity — flagged LOW confidence in the trade's own notes wherever it's used.
- **Approval is a policy, not the user's real judgment** — {latency} minute
  latency, {rate:.0%} approval rate. This tests the machinery, not "would I
  have said yes."
- **No cross-letter position tracking.** Each letter compiles independently;
  a real-world position Crown re-lists week over week is treated as a new
  trade each time, guarded only by "don't open a second position in a symbol
  that's already open" — it does not reconcile stop/target updates a later
  letter might have made to an already-open position.
- **Seven letters is a machinery shakedown, not a statistical validation**
  of the letter's edge. This run tells you whether the system faithfully
  and safely executes what the letter says — not whether following the
  letter is profitable.
"""


def _fmt_money(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def generate_report(result: BacktestResult, cfg: Config, start: date, end: date) -> str:
    lines: list[str] = []
    lines.append(f"# Crown Letter Backtest — {start} to {end}\n")
    lines.append(f"Letters compiled: {len(result.letters)}. Sleeve: {_fmt_money(cfg.sizing.sleeve)}. "
                 f"Risk at 1.00 sizing: {_fmt_money(cfg.sizing.risk_at_1_00)}.\n")

    total_pnl = sum(
        rt.position.realized_pnl for rt in result.runtimes.values()
        if rt.position is not None
    )
    closed = [rt for rt in result.runtimes.values() if rt.status == "closed"]
    open_at_end = [rt for rt in result.runtimes.values() if rt.status == "open"]
    lines.append(f"**Total realized P&L: {_fmt_money(total_pnl)}** across {len(closed)} closed "
                 f"position(s); {len(open_at_end)} still open at the end of the run.\n")

    lines.append("## Per-letter trade outcomes\n")
    for letter, plan in zip(result.letters, result.plans):
        lines.append(f"### {letter.letter_date}{' (mid-cycle)' if letter.is_mid_cycle else ''}\n")
        lines.append("| Symbol | Source | Structure | Outcome | Size method | P&L | Notes |")
        lines.append("|---|---|---|---|---|---|---|")
        for trade in plan.trades:
            rt = result.runtimes.get(trade.trade_id)
            sym = trade.traded_symbol or trade.crown_symbol
            if rt is None:
                outcome, pnl, notes = "skipped (compile time)", None, trade.skip_reason or ""
            else:
                outcome = rt.status
                pnl = rt.position.realized_pnl if rt.position else None
                notes = rt.close_note or ""
            conviction = f" [{trade.conviction}]" if trade.conviction else ""
            flag = " ⚠glyph-mismatch" if trade.conviction_flag == "glyph_count_mismatch" else ""
            lines.append(
                f"| {sym}{conviction}{flag} | {trade.source} | {trade.structure} | {outcome} | "
                f"{trade.size.method} | {_fmt_money(pnl)} | {notes} |"
            )
        lines.append("")

    lines.append("## Alternative Finder activity\n")
    for letter, plan in zip(result.letters, result.plans):
        for trade in plan.trades:
            if not trade.alt_finder_log:
                continue
            sym = trade.traded_symbol or trade.crown_symbol
            lines.append(f"**{letter.letter_date} — {sym}** ({trade.structure}):")
            for entry in trade.alt_finder_log:
                lines.append(f"  - {entry}")
            lines.append("")

    lines.append("## Crown-structure vs sized-alternative scorecard\n")
    lines.append(
        "06-sizing-and-alternatives.md calls for logging Crown's exact structure's P&L "
        "against this system's sleeve-sized alternative every week. This backtest can only "
        "report the second half — Crown's own realized P&L on his stated structure isn't in "
        "the letter schema (it would need his mid-week fills or a following letter's "
        "'previous week review' matched back to this trade, which isn't implemented in v1). "
        "The table below is what *this system* would have done at your sleeve.\n"
    )
    lines.append("| Symbol | Letter | Crown's structure | Our method | Our P&L |")
    lines.append("|---|---|---|---|---|")
    for letter, plan in zip(result.letters, result.plans):
        for trade in plan.trades:
            if trade.size.method not in ("crown_exact", "equity_proxy", "equity_proxy_pending_price"):
                continue
            rt = result.runtimes.get(trade.trade_id)
            pnl = rt.position.realized_pnl if rt and rt.position else None
            sym = trade.traded_symbol or trade.crown_symbol
            lines.append(f"| {sym} | {letter.letter_date} | {trade.structure} | {trade.size.method} | {_fmt_money(pnl)} |")
    lines.append("")

    lines.append("## Escalations and schedule tightening\n")
    if result.escalations:
        lines.append("| Time | Type | Symbol | Detail |")
        lines.append("|---|---|---|---|")
        for e in result.escalations:
            detail = {k: v for k, v in e.items() if k not in ("ts", "type", "symbol")}
            lines.append(f"| {e['ts']} | {e['type']} | {e.get('symbol', '')} | {detail} |")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append("## Guardrail rejections\n")
    rejections = [rt for rt in result.runtimes.values() if rt.status == "rejected_guardrail"]
    if rejections:
        lines.append("| Trade | Symbol | Reason |")
        lines.append("|---|---|---|")
        for rt in rejections:
            sym = rt.trade.traded_symbol or rt.trade.crown_symbol
            lines.append(f"| {rt.trade.trade_id} | {sym} | {rt.close_note} |")
    else:
        lines.append("None — every staged order that reached the gateway passed the deterministic guardrails.")
    lines.append("")

    lines.append(KNOWN_APPROXIMATIONS.format(
        default_proxy_stop_pct=cfg.alternative_finder.default_proxy_stop_pct,
        latency=cfg.backtest.approval_latency_minutes,
        rate=cfg.backtest.approval_rate,
    ))

    return "\n".join(lines)


def write_report(result: BacktestResult, cfg: Config, start: date, end: date) -> Path:
    cfg.paths.reports.mkdir(parents=True, exist_ok=True)
    path = cfg.paths.reports / f"backtest_{start}_{end}.md"
    path.write_text(generate_report(result, cfg, start, end), encoding="utf-8")
    return path
