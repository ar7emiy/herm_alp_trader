"""Phase 0 commands (spec v3):
  crown llm-check               — list OpenAI models the key can use; ping the extract model
  crown extract <pdf> | --all   — vision-extract letters into the thesis-first model (cached)
  crown inventory               — aggregate extractions into the Phase 0 inventory report
  crown audit <pdf>             — offline validation and page-review report (no approval)
  crown probe                   — read-only Alpaca account/capability probe

  crown backtest --from --to    — v3 cash-only historical research replay

Legacy v1 prototype commands (superseded spec; replaced in Phase 1+):
  crown legacy-compile <pdf>    — v1 parse + contingent plan (Anthropic)
  crown legacy-backtest        — v1 replay, explicitly opt-in
  crown report <path>           — print a v1 backtest report
"""
from __future__ import annotations

import functools
from datetime import date
from pathlib import Path

import click

from crown.config import load_config


@click.group()
def cli():
    pass


def _friendly(fn):
    """Missing-credential and bad-config errors are common on a first run —
    surface RuntimeError's message directly instead of a Python traceback."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except RuntimeError as e:
            raise click.ClickException(str(e))
    return wrapper


# ---------------------------------------------------------------------------
# Phase 0
# ---------------------------------------------------------------------------

@cli.command("llm-check")
@_friendly
def llm_check():
    """List the OpenAI models this key can use and make one tiny call to the
    configured extract model."""
    from openai import OpenAI

    from crown.config import openai_api_key
    from crown.diagnostics import error_label, safe_error

    cfg = load_config()
    client = OpenAI(api_key=openai_api_key())
    try:
        ids = sorted(m.id for m in client.models.list())
    except Exception as exc:
        raise RuntimeError(f"OpenAI model access failed: {error_label(safe_error(exc))}") from None
    chat_like = [i for i in ids if i.startswith(("gpt-", "o1", "o3", "o4", "o5"))]
    click.echo(f"{len(ids)} models visible; GPT/o-series ({len(chat_like)}):")
    for i in chat_like:
        click.echo(f"  {i}")
    click.echo("")
    model = cfg.llm.extract_model
    if model not in ids:
        raise click.ClickException(
            f"config.yaml llm.extract_model '{model}' is not available to this key — pick one above")
    try:
        resp = client.responses.create(model=model, input="Reply with the single word: ok",
                                       max_output_tokens=1024, store=False)
    except Exception as exc:
        raise RuntimeError(f"OpenAI request failed: {error_label(safe_error(exc))}") from None
    click.echo(f"Ping {model}: status={resp.status}")


@cli.command("extract")
@click.argument("pdf_path", required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--all", "all_letters", is_flag=True, help="Extract every letter in crown_letters/.")
@click.option("--force", is_flag=True, help="Re-extract even if a cached result exists.")
@_friendly
def extract(pdf_path: Path | None, all_letters: bool, force: bool):
    """Vision-extract letter(s) into the doc 03 model. Cached per extractor
    version; costs one OpenAI call per uncached letter."""
    from crown.letter.extract import extract_letter, letter_pdfs, load_cached
    from crown.diagnostics import error_label, safe_error

    cfg = load_config()
    if all_letters == bool(pdf_path):
        raise click.UsageError("pass exactly one of PDF_PATH or --all")
    pdfs = letter_pdfs(cfg) if all_letters else [pdf_path]

    failures = []
    for pdf in pdfs:
        cached = not force and load_cached(cfg, pdf) is not None
        click.echo(f"{pdf.name}: {'cached' if cached else 'extracting ...'}")
        try:
            rec = extract_letter(pdf, cfg=cfg, force=force)
        except Exception as e:  # noqa: BLE001 — keep going; report every failure at the end
            reason = error_label(safe_error(e))
            failures.append((pdf.name, reason))
            click.echo(f"  FAILED: {reason}")
            continue
        x = rec.extraction
        u = rec.usage or {}
        click.echo(f"  {x.letter_date}  theses={len(x.theses)} positions={len(x.crown_positions)} "
                   f"updates={len(x.lifecycle_updates)} levels={len(x.levels)} "
                   f"flags={len(x.review_flags)}  tokens={u.get('input_tokens', '?')}/{u.get('output_tokens', '?')}")
    if failures:
        raise click.ClickException(f"{len(failures)} letter(s) failed: " + "; ".join(n for n, _ in failures))


@cli.command("inventory")
@_friendly
def inventory():
    """Write the Phase 0 inventory report from cached extractions (no API calls)."""
    from crown.letter.inventory import write_report

    cfg = load_config(load_env=False)
    path = write_report(cfg)
    click.echo(f"Inventory written to {path}")


@cli.command("prepare")
@click.option("--workers", type=click.IntRange(1, 4), default=4, show_default=True)
@click.option("--offline", is_flag=True, help="Refresh audits from caches only; no credentials or API calls.")
@_friendly
def prepare(workers: int, offline: bool):
    """Bounded corpus extraction and independent facts; research review only."""
    from crown.letter.prepare import prepare_corpus

    result = prepare_corpus(load_config(load_env=not offline), workers, lambda message: click.echo(message), offline)
    if any(r["status"] == "failed" for r in result["results"]):
        raise click.ClickException("Some letters failed; successful reads are cached, no execution enabled")


@cli.command("audit")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--review-file", type=click.Path(exists=True, path_type=Path),
              help="Private page-review JSON bound to this exact extraction hash.")
@_friendly
def audit(pdf_path: Path, review_file: Path | None):
    """Check a cached extraction offline. This command never grants approval."""
    from pypdf import PdfReader

    from crown.letter.audit import PageReview, audit_record, write_audit
    from crown.letter.extract import load_cached

    cfg = load_config(load_env=False)
    record = load_cached(cfg, pdf_path)
    if record is None:
        raise click.ClickException("No matching extraction cache; extract this PDF first")
    try:
        review = PageReview.model_validate_json(review_file.read_text(encoding="utf-8")) if review_file else None
        report = audit_record(record, len(PdfReader(pdf_path).pages), review)
    except ValueError:
        raise click.ClickException("Invalid review or extraction mismatch; no audit approval was granted") from None
    out = write_audit(report, cfg.paths.reports)
    click.echo(f"Audit: {report.planning_status}; issues={len(report.issues)}")
    click.echo(f"Private report: {out}")
    if report.issues:
        raise click.ClickException("Unresolved audit findings; extraction remains unapproved")


@cli.command("preview")
@_friendly
def preview():
    """Generate a private HTML report from cached letters and audits, offline."""
    from crown.letter.preview import write_preview

    path = write_preview(load_config(load_env=False))
    click.echo(f"Private results: {path}")
    click.echo("Research preview only; no orders or approved allocations")


@cli.command("correct")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--correction-file", required=True, type=click.Path(exists=True, path_type=Path))
@_friendly
def correct(pdf_path: Path, correction_file: Path):
    """Apply private source corrections; preserve the original attempt and provenance."""
    from crown.letter.correct import CorrectionBatch, apply_corrections

    try:
        batch = CorrectionBatch.model_validate_json(correction_file.read_text(encoding="utf-8"))
        record = apply_corrections(pdf_path, batch, load_config(load_env=False))
    except ValueError:
        raise click.ClickException("Invalid correction, source, or extraction mismatch; nothing changed") from None
    click.echo(f"Recorded {len(batch.changes)} source corrections; batches={len(record.corrections)}")
    click.echo("Prior page reviews no longer match this revision; re-audit required. No approval granted.")


@cli.command("probe")
@_friendly
def probe():
    """GET-only PAPER probe: account observations, assets, data feeds, and
    news. Does not certify settled cash or order support. Places no orders."""
    from crown.probe import run_probe, summarize, write_probe

    cfg = load_config()
    report = run_probe(cfg)
    path = write_probe(cfg, report)
    for line in summarize(report):
        click.echo(line)
    click.echo(f"Full probe written to {path}")


# ---------------------------------------------------------------------------
# Historical research
# ---------------------------------------------------------------------------

@cli.command("backtest")
@click.option("--from", "start", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--to", "end_date", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--policy", "policy_name", type=click.Choice(["strict","protective_research","both"]), default="both", show_default=True)
@click.option("--offline", is_flag=True, help="Use hash-validated caches only; never load credentials or call APIs.")
@click.option("--out", type=click.Path(path_type=Path), help="New output folder under data/reports/ (must not already exist).")
@_friendly
def research_backtest(start, end_date, policy_name, offline, out):
    """Replay v3 letters with a settled-cash ledger and explicit research limits."""
    from crown.research.app import backtest

    path, results=backtest(load_config(load_env=not offline),start.date(),end_date.date(),policy_name,offline,out)
    for r in results:
        click.echo(f"{r['policy']['name']}: entries={r['counts'].get('entry_fill',0)}, "
                   f"net price P&L=${float(r['net_pnl']):,.2f}, drawdown={float(r['max_drawdown_pct']):.2f}%")
    click.echo(f"Private results: {path}")
    click.echo("Historical research only; no broker orders or paper permissions enabled.")

@cli.command("legacy-compile")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--force", is_flag=True, help="Re-parse even if a cached result exists.")
@_friendly
def compile_letter(pdf_path: Path, force: bool):
    """[legacy v1] Parse a letter PDF and build its contingent plan."""
    from crown.compiler.build_plan import build_plan
    from crown.compiler.parse_letter import parse_letter

    cfg = load_config()
    click.echo(f"Parsing {pdf_path.name} ...")
    letter = parse_letter(pdf_path, cfg=cfg, force=force)
    plan = build_plan(letter, cfg)

    plan_path = cfg.paths.plans / f"{pdf_path.stem}.plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    click.echo(f"Letter date: {letter.letter_date} (mid-cycle: {letter.is_mid_cycle})")
    click.echo(f"Plan written to {plan_path}")
    click.echo("")
    for trade in plan.trades:
        sym = trade.traded_symbol or trade.crown_symbol
        click.echo(f"  [{trade.action:10s}] {sym:8s} {trade.structure:14s} "
                   f"method={trade.size.method:26s} reason={trade.skip_reason or ''}")


@cli.command("legacy-backtest")
@click.option("--from", "start", type=str, default=None,
              help="YYYY-MM-DD, defaults to config.yaml backtest.start_date")
@click.option("--to", "end_date", type=str, default=None,
              help="YYYY-MM-DD, defaults to compiling through the latest letter found")
@_friendly
def backtest(start: str | None, end_date: str | None):
    """[legacy v1] Replay every letter against historical bars."""
    from crown.backtest.report import write_report
    from crown.backtest.runner import BacktestRunner

    cfg = load_config()
    start_d = date.fromisoformat(start) if start else date.fromisoformat(cfg.backtest.start_date)
    end_d = date.fromisoformat(end_date) if end_date else (
        date.fromisoformat(cfg.backtest.end_date) if cfg.backtest.end_date else None
    )

    click.echo(f"Backtesting {start_d} to {end_d or 'latest letter + watch window'} ...")
    runner = BacktestRunner(cfg)
    result = runner.run(start=start_d, end=end_d)

    report_end = end_d or (date.fromisoformat(result.letters[-1].letter_date) if result.letters else start_d)
    report_path = write_report(result, cfg, start_d, report_end)

    total_pnl = sum(rt.position.realized_pnl for rt in result.runtimes.values() if rt.position is not None)
    n_closed = sum(1 for rt in result.runtimes.values() if rt.status == "closed")
    n_open = sum(1 for rt in result.runtimes.values() if rt.status == "open")
    click.echo(f"Letters compiled: {len(result.letters)}")
    click.echo(f"Closed positions: {n_closed}, still open: {n_open}, total realized P&L: ${total_pnl:,.2f}")
    click.echo(f"Report written to {report_path}")


@cli.command()
@click.argument("report_path", type=click.Path(exists=True, path_type=Path))
def report(report_path: Path):
    """[legacy v1] Print a previously-written backtest report."""
    click.echo(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    cli()
