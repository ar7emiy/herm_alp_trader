"""Private, offline extraction results. This is not an execution dashboard.

No provider calls, order intents, allocation engine, or credentials are used.
Cash feasibility is a preliminary screen of the exact Crown structure only.
"""
from __future__ import annotations

import html
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from crown.config import Config
from crown.letter.audit import ExtractionAudit, record_hash
from crown.letter.inventory import load_all, needs_substitution
from crown.letter.schema import CrownPosition, ExtractionRecord


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def risk_budget(p: CrownPosition, cfg: Config) -> Decimal | None:
    if p.sizing_multiplier is None:
        return None
    return Decimal(str(cfg.sizing.risk_at_1_00)) * Decimal(str(p.sizing_multiplier))


def preliminary_screen(p: CrownPosition) -> tuple[str, list[str]]:
    """Do not equate a compatible wrapper with an approved entry or substitute."""
    if p.status == "closed" or p.setup_state == "closed":
        return "Closed / history", ["Crown lists this position as closed"]
    if p.setup_state != "active":
        return "Watch / review", [f"Setup state: {p.setup_state}; no active entry instruction"]
    reasons = needs_substitution(p)
    if reasons:
        return "Substitute or skip", reasons
    if any(leg.action == "sell" for leg in p.structure.legs):
        return "Capability review", ["Package includes a short leg; account and payoff checks remain required"]
    if p.instrument.asset_class not in {"equity", "etf"}:
        return "Capability review", ["Instrument is outside the first stock/ETF release"]
    if p.view != "bullish" or p.structure.type != "long":
        return "Fidelity review", ["Direction and payoff need explicit review"]
    return "Wrapper candidate", [
        "A long stock/ETF wrapper may fit cash policy; tradability, sizing, data, exits and timing remain unverified"
    ]


def _audits(cfg: Config, records: list[ExtractionRecord]) -> dict[str, ExtractionAudit]:
    wanted = {record_hash(r) for r in records}
    latest: dict[str, ExtractionAudit] = {}
    for path in (cfg.paths.reports / "audits").glob("*.json"):
        try:
            report = ExtractionAudit.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue  # superseded audit schema remains on disk
        if report.extraction_sha256 not in wanted:
            continue
        old = latest.get(report.extraction_sha256)
        if old is None or report.audited_at > old.audited_at:
            latest[report.extraction_sha256] = report
    return latest


def usage_summary(cfg: Config) -> dict:
    """Count active and archived attempts once, including failed page audits.

    Pricing is explicitly dated/limited to the configured GPT-5 family snapshot.
    This estimate is not a provider invoice. Unknown model usage is not priced.
    """
    seen = set()
    totals = Counter()
    cost = Decimal("0")
    unknown_models = set()
    for path in cfg.paths.extracted_letters.rglob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            identity = (record["pdf_sha256"], record["extracted_at"],
                        record.get("request_fingerprint"), record["model"])
            if identity in seen:
                continue
            seen.add(identity)
            usage = record.get("usage") or {}
            tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            totals.update(attempts=1, input_tokens=tin, output_tokens=tout)
            if record["model"] not in {"gpt-5", "gpt-5-2025-08-07"}:
                unknown_models.add(record["model"])
                continue
            cost += (Decimal(tin - cached) * Decimal("1.25")
                     + Decimal(cached) * Decimal("0.125")
                     + Decimal(tout) * Decimal("10")) / Decimal(1_000_000)
        except (ValueError, KeyError, TypeError):
            continue
    return dict(totals, estimated_usd=str(cost), unpriced_models=sorted(unknown_models))


def build_preview(cfg: Config) -> str:
    records, missing = load_all(cfg)
    audits = _audits(cfg, records)
    usage = usage_summary(cfg)
    positions = [p for r in records for p in r.extraction.crown_positions]
    counts = Counter(preliminary_screen(p)[0] for p in positions)
    audited = sum(audits.get(record_hash(r)) is not None for r in records)
    checked = sum(audits[record_hash(r)].transcription_status == "critical_checks_passed"
                  for r in records if record_hash(r) in audits)
    options = '<option value="all">All extracted letters</option>' + "".join(
        f'<option value="{_e(r.extraction.letter_date)}">{_e(r.extraction.letter_date)}</option>'
        for r in records)
    sections = []
    for r in reversed(records):
        x = r.extraction
        audit = audits.get(record_hash(r))
        status = audit.transcription_status.replace("_", " ") if audit else "Not page reviewed"
        correction_count = sum(len(batch.get("changes", [])) for batch in r.corrections)
        theses = {t.thesis_key: t for t in x.theses}
        rows = []
        for p in x.crown_positions:
            title, reasons = preliminary_screen(p)
            budget = risk_budget(p, cfg)
            budget_text = "Not stated" if budget is None else f"${budget:,.2f} risk budget"
            thesis = theses.get(p.thesis_key)
            invalidations = "<li>None stated; an approved fallback exit is required</li>"
            if thesis and thesis.invalidation:
                invalidations = "".join(f"<li>{_e(c.text)} (page {_e(c.source.page)})</li>"
                                        for c in thesis.invalidation)
            terms = "".join(f"<li><strong>{_e(t.label)}:</strong> {_e(t.raw)} "
                            f'<span class="muted">p. {_e(t.source.page)}</span></li>'
                            for t in p.reported_terms)
            pdf_link = f"../../crown_letters/{quote(r.pdf_name)}#page={p.source.page or 1}"
            leg_text = "; ".join(
                f"{leg.action} {leg.underlying} {leg.right}"
                + (f" {leg.strike:g}" if leg.strike is not None else "")
                + (f" · {leg.expiry}" if leg.expiry else "")
                + (f" · {leg.ratio:g} {leg.ratio_kind}" if leg.ratio is not None else "")
                for leg in p.structure.legs)
            rows.append(f'''<article class="position" data-state="{_e(title)}">
<div class="position-head"><div><span class="eyebrow">{_e(p.view)} · {_e(p.structure.type)}</span>
<h3>{_e(p.instrument.primary)}</h3></div><span class="tag">{_e(title)}</span></div>
<p>{_e(thesis.claim if thesis else "Thesis reference unresolved")}</p>
<div class="facts"><span>{_e(budget_text)}</span><span>Crown: {_e(p.status)}</span>
<span>Setup: {_e(p.setup_state)}</span><a href="{_e(pdf_link)}">Source p. {_e(p.source.page)}</a></div>
<p class="muted">{_e('; '.join(reasons))}</p>
<details><summary>Terms, thesis breaks and implementation gaps</summary>
<p>{_e(leg_text or "No explicit legs")}</p><h4>Crown's printed terms</h4>
<ul>{terms or '<li>No structured terms extracted; review the source</li>'}</ul>
<h4>What could break the thesis</h4><ul>{invalidations}</ul>
<p><strong>Stated stop:</strong> {_e(p.stop.rule or p.stop.level or p.stop.type)}</p>
<p>{_e(p.management_notes or "No management note extracted")}</p>
<p class="muted">Risk budgets are not cash allocations or trade quantities. No new entry is approved here.</p>
</details></article>''')
        findings = "".join(f"<li><strong>{_e(i.code)}:</strong> {_e(i.detail)}</li>"
                           for i in (audit.issues if audit else []))
        checks = audit.page_review.checks if audit and audit.page_review else []
        checks_html = "".join(f"<li>{'PASS' if c.passed else 'FAIL'} · {_e(c.name)}: {_e(c.detail)}</li>" for c in checks)
        attached = {p.thesis_key for p in x.crown_positions}
        unexpressed = [t for t in x.theses if t.thesis_key not in attached]
        thesis_context = "".join(f"<li><strong>{_e(t.title)}:</strong> {_e(t.claim)} "
                                 f"(page {_e(t.source.page)}; no concrete position extracted)</li>" for t in unexpressed)
        sections.append(f'''<section class="letter" data-date="{_e(x.letter_date)}">
<div class="letter-head"><div><h2>{_e(x.letter_date)}</h2><p class="muted">{_e(r.pdf_name)} · {_e(r.extractor_version)} · {_e(r.model)}</p></div>
<span class="tag">{_e(status)}</span></div>
<p>{len(x.theses)} theses · {len(x.crown_positions)} position rows · {len(x.lifecycle_updates)} lifecycle updates · {len(x.levels)} reference levels</p>
<p class="muted">{correction_count} source-checked correction edits in {len(r.corrections)} batches. Original provider output is preserved. Critical checks cover listed fields; this is not a claim of complete automated extraction accuracy.</p>
{''.join(rows)}
{('<details><summary>Macro theses without a concrete instrument setup</summary><ul>'+thesis_context+'</ul></details>') if unexpressed else ''}
<details class="audit"><summary>Page audit and unresolved inputs ({len(audit.issues) if audit else 0})</summary>
<ul>{checks_html}</ul><ul>{findings or '<li>No audit available; manual page review remains due</li>'}</ul></details>
<details><summary>Closures and changes reported by Crown</summary><ul>
{''.join(f'<li><strong>{_e(u.position_ref)} · {_e(u.kind)}:</strong> {_e(u.detail)} (p. {_e(u.source.page)})</li>' for u in x.lifecycle_updates)}
</ul></details></section>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Crown · Private input results</title><style>
:root{{--ink:#20382e;--muted:#637168;--line:#d8dfda;--paper:#f4f6f2;--accent:#287553}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,sans-serif}}
main{{max-width:1180px;margin:auto;padding:36px 30px}}h1{{font-size:32px;letter-spacing:-1px;margin:8px 0}}h2{{font-size:24px;margin:0}}h3{{font-size:20px;margin:3px 0}}h4{{margin:18px 0 4px}}p{{margin:10px 0}}a{{color:var(--accent)}}
.eyebrow{{text-transform:uppercase;letter-spacing:1.5px;font-size:11px;font-weight:700}}.muted{{color:var(--muted)}}
.notice{{border-left:3px solid var(--accent);padding:12px 18px;background:white;margin:22px 0}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin:28px 0}}
.metric{{border-top:1px solid var(--line);padding-top:12px}}.metric b{{display:block;font-size:28px;font-weight:600}}.metric span{{color:var(--muted)}}
.controls{{display:flex;gap:12px;padding:16px 0;position:sticky;top:0;background:var(--paper);border-bottom:1px solid var(--line);z-index:1}}
select,input{{padding:10px 12px;border:1px solid var(--line);background:white;color:var(--ink);font:inherit}}input{{flex:1;min-width:140px}}
.letter{{margin:38px 0 48px}}.letter-head,.position-head{{display:flex;justify-content:space-between;gap:18px;align-items:start}}
.tag{{font-size:11px;white-space:nowrap;padding:4px 8px;border:1px solid var(--line);border-radius:3px;text-transform:uppercase}}
.position{{background:white;padding:20px 24px;margin:12px 0;border:1px solid var(--line)}}.facts{{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;font-weight:600;color:var(--accent)}}
details{{margin:12px 0}}summary{{cursor:pointer;font-weight:600}}li{{margin:5px 0}}.audit{{padding:14px 0;border-bottom:1px solid var(--line)}}
footer{{border-top:1px solid var(--line);padding-top:18px;font-size:12px;color:var(--muted)}}[hidden]{{display:none!important}}
@media(max-width:700px){{main{{padding:22px 16px}}.metrics{{grid-template-columns:repeat(2,1fr)}}.letter-head,.position-head{{display:block}}.tag{{display:inline-block;margin:6px 0}}.controls{{flex-wrap:wrap}}}}
</style></head><body><main><header><span class="eyebrow">Crown / Hermes · Phase 0 input results</span>
<h1>From letters to an explainable book</h1><p class="muted">Private local report · ${cfg.sizing.sleeve:,.0f} cash-account policy · OpenAI extraction</p></header>
<div class="notice"><strong>Research preview. Orders disabled.</strong> These are extracted Crown ideas and preliminary constraints, not approved allocations or backtest returns. Historical letter observations are not current market data. Unknown receipt times prevent causal replay.</div>
<div class="metrics"><div class="metric"><b>{len(records)}</b><span>Letters extracted / {len(records)+len(missing)} available</span></div>
<div class="metric"><b>{len(positions)}</b><span>Position rows; repeated holdings may recur</span></div>
<div class="metric"><b>{checked} / {audited}</b><span>Critical checks passed / page audits</span></div>
<div class="metric"><b>${Decimal(usage['estimated_usd']):.3f}</b><span>Estimated spend for recorded extraction attempts</span></div></div>
<p class="muted">{_e(' · '.join(f'{name}: {count}' for name,count in counts.items()))}. No wrapper candidate is trade-ready.</p>
<div class="controls"><select id="letter-filter" aria-label="Letter date">{options}</select><input id="search" type="search" placeholder="Find a ticker, thesis or constraint" aria-label="Search results"></div>
{''.join(sections) or '<p>No current extraction caches are available.</p>'}
<footer>Generated offline from private caches. No API calls, account mutations or orders. {_e(usage.get('attempts',0))} extraction attempts · {_e(usage.get('input_tokens',0))} input / {_e(usage.get('output_tokens',0))} output tokens.
Cost estimate uses <a href="https://developers.openai.com/api/docs/models/gpt-5">published GPT-5 rates</a> verified 2026-10-19 ($1.25/M input, $0.125/M cached input, $10/M output); other models are unpriced. This is not a billing invoice. Source PDFs and this report remain outside git.</footer>
</main><script>
const dateFilter=document.getElementById('letter-filter'), search=document.getElementById('search');
function filter(){{const q=search.value.toLowerCase();document.querySelectorAll('.letter').forEach(section=>{{
const dateMatch=dateFilter.value==='all'||section.dataset.date===dateFilter.value;let matches=0;
section.querySelectorAll('.position').forEach(row=>{{row.hidden=!!q&&!row.textContent.toLowerCase().includes(q);if(!row.hidden)matches++;}});
section.hidden=!dateMatch||(!!q&&!matches);}});}}
dateFilter.addEventListener('change',filter);search.addEventListener('input',filter);
</script></body></html>'''


def write_preview(cfg: Config) -> Path:
    out = cfg.paths.reports / "phase0-results.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_preview(cfg), encoding="utf-8")
    return out
