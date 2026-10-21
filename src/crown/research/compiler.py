"""Deterministic, deliberately narrow expressions from frozen v3 source reads.

Only an identical long stock/ETF is executable. Unsupported exposures get a skip;
there is no speculative directional conversion of options, pairs or short vol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re

from pypdf import PdfReader

from crown.data.calendar import market_open_close, next_trading_day, trading_days
from crown.letter.audit import audit_record, record_hash
from crown.letter.extract import letter_pdfs, load_cached
from crown.letter.verify import corroboration, load_verification, match_fact, printed_entry
from crown.research.models import D, Plan, ResearchPolicy, digest


@dataclass(frozen=True)
class LetterBatch:
    available_at: datetime
    letter_date: str
    plans: tuple[Plan, ...]
    closures: tuple[str, ...]
    timing_assumption: str


def availability(record, approval_minutes=45):
    if approval_minutes < 0:
        raise ValueError("Approval latency cannot be negative")
    if record.received_at:
        at = datetime.fromisoformat(record.received_at.replace("Z", "+00:00"))
        if not at.tzinfo:
            raise ValueError("Receipt timestamp must be timezone aware")
        assumption = "Recorded receipt plus simulated approval latency"
    else:
        at = market_open_close(next_trading_day(record.extraction.letter_date))[0]
        assumption = "Receipt unknown: assume next NYSE open strictly after printed date, then approval latency"
    return at + timedelta(minutes=approval_minutes), assumption


def compile_records(sources, policy: ResearchPolicy, approval_minutes=45):
    """Pure compiler; sources = (record, independent_read, PDF page_count).

    A stable episode persists until an explicit independently read Crown closure.
    A stopped-out episode cannot be re-entered merely because it appears again.
    """
    active, episodes, batches, last_keys = {}, {}, [], {}
    latest_date = ""
    # Episode identity must follow information availability, not printed dates.
    # A delayed old closure cannot create a new earlier episode retroactively.
    for rec, verified, pages in sorted(sources, key=lambda r: (availability(r[0], approval_minutes)[0], r[0].extraction.letter_date)):
        x = rec.extraction
        at, timing = availability(rec, approval_minutes)
        if x.letter_date < latest_date:
            batches.append(LetterBatch(at, x.letter_date, (), (), "Late older letter; no episode or plan mutation"))
            continue
        latest_date = x.letter_date
        days = trading_days(at.date(), at.date() + timedelta(days=30))
        expiry = days[min(policy.max_letter_age_sessions - 1, len(days)-1)]
        rhash = record_hash(rec)
        check=corroboration(rec, verified, pages)
        agreement = {r["position_key"]: r for r in check["positions"]}
        facts = verified.reading.trades
        audit_errors=[issue for issue in audit_record(rec,pages).issues if issue.severity=="error"]
        closures = []
        # Full structure is part of the identity: closing an option overlay never
        # closes the stock core. Only independently read plain-long closures count.
        for fact in facts:
            if (fact.state == "closed" and fact.kind == "long_stock_etf" and check["pages_complete"] and check["date_agrees"]
                    and fact.source.page and 1<=fact.source.page<=pages and fact.source.quote.strip()):
                matches = [sig for sig in active if len(sig)==2 and sig[0]=="long" and sig[1] in fact.symbols]
                if len(matches) == 1:
                    sig=matches[0]
                    confirmed=any(p.status=="closed" and p.structure.type=="long" and p.instrument.primary.upper()==sig[1]
                                  for p in x.crown_positions)
                    confirmed |= any(u.kind in {"closed","invalidated"} and u.position_ref in {sig[1],last_keys.get(sig)}
                                     for u in x.lifecycle_updates)
                    if confirmed:
                        closures.append(active.pop(sig))
        plans = []
        for index,p in enumerate(x.crown_positions):
            plain = p.structure.type == "long" and p.instrument.asset_class in {"equity", "etf"}
            symbol = p.instrument.primary.upper()
            sig = ("long", symbol) if plain else (p.structure.type, symbol, digest(p.structure.legs))
            if sig not in active:
                episodes[sig] = episodes.get(sig, 0)+1
                active[sig] = digest((sig, episodes[sig]))[:20]
            pid = active[sig]
            last_keys[sig]=p.position_key
            reasons, deviations, invalidations = [], [], []
            if any(issue.field in {"page_coverage","letter_date","theses","crown_positions"}
                   or f"crown_positions[{index}]" in issue.field for issue in audit_errors):
                reasons.append("Structural source audit error affects this plan or letter coverage")
            f = match_fact(p, facts) if plain else None
            if not plain or p.view != "bullish" or any(l.action != "buy" or l.right != "stock" for l in p.structure.legs):
                reasons.append("Unsupported payoff: research supports identical plain long stocks/ETFs only")
            if (p.hedges_position_keys or any(p.position_key in other.hedges_position_keys for other in x.crown_positions)
                    or any(other.position_key!=p.position_key and other.thesis_key==p.thesis_key
                           and other.status!="closed" and other.structure.type!="long" for other in x.crown_positions)):
                reasons.append("Linked hedge/compound thesis cannot be reproduced; do not silently remove its other legs")
            if p.status == "closed" or p.setup_state != "active":
                reasons.append(f"Non-executable Crown setup: {p.status}/{p.setup_state}")
            if p.status == "conditional" or p.entry.type == "conditional" or p.entry.condition or p.event_dependency:
                reasons.append("Conditional/event entry requires an unavailable validated trigger")
            if p.entry.band_low is not None or p.entry.band_high is not None:
                reasons.append("Entry band is not a single validated execution anchor")
            if plain and (not agreement.get(p.position_key, {}).get("agrees")):
                reasons.append("Independent execution facts disagree or coverage is incomplete")
            if f and (f.no_add or f.state not in {"held", "planned"}):
                reasons.append("Crown prohibits additions or this is not an executable held/planned setup")
            if re.search(r"no (?:new |further )?(?:additions|exposure)|rather than adding|hold existing only|carry.only",
                         p.management_notes or "", re.IGNORECASE):
                reasons.append("Literal Crown management restriction prohibits adding exposure")
            if not p.source.page or not 1 <= p.source.page <= pages or not p.source.quote.strip():
                reasons.append("Missing valid source page and quote")
            if x.review_flags:
                # A per-position execution disagreement always blocks. General
                # extraction flags stay visible in the manifest, not erased.
                deviations.append("Research-only independent corroboration; unresolved extraction flags retained")
            anchor = printed_entry(f) if f else p.entry.level
            if anchor is None:
                if not policy.allow_unanchored_holdings or not f or f.state != "held":
                    reasons.append("No printed underlying entry anchor")
                else:
                    deviations.append("Tier-2: initiate an existing Crown holding at our later open; historical entry unknown")
            multiplier = p.sizing_multiplier
            if multiplier is None:
                multiplier = .25
                deviations.append("Tier-1: unstated risk units use fixed conservative 0.25 RU; never conviction-derived")
            kind, sessions = (f.stop_kind, f.stop_sessions) if f else ("none", None)
            stop = f.stop_price if f else None
            if not policy.atr_protection and (kind not in {"touch", "close"} or stop is None or (kind=="close" and not sessions)):
                reasons.append("No unambiguous protective observation rule/session count")
            if policy.atr_protection:
                deviations.append(f"Tier-2 shadow only: higher of printed numeric stop and {policy.atr_multiple} ATR; ambiguous convention becomes touch; {policy.trail_multiple} ATR close-based trail after +1R; share increment {policy.share_step}")
                # Unknown consecutive-close count is never invented for a source stop.
                if kind == "close" and not sessions:
                    kind = "ambiguous"
            thesis = next((t for t in x.theses if t.thesis_key == p.thesis_key), None)
            if not thesis:
                reasons.append("Missing Crown thesis")
            for inv in thesis.invalidation if thesis else []:
                if (inv.kind == "price_level" and inv.instrument and inv.instrument.upper()==symbol
                        and inv.op in {"close_below", "trade_below"} and inv.level is not None
                        and inv.band_low is None and inv.band_high is None
                        and (inv.op == "trade_below" or inv.sessions)):
                    invalidations.append((inv.op, D(inv.level), inv.sessions or 1))
                else:
                    reasons.append("Thesis invalidation requires unavailable/ambiguous reference or narrative evidence")
            if f and not (thesis and thesis.invalidation):
                for text in f.invalidations:
                    if not re.fullmatch(r"(?i)hold(?:; trail risk tighter after recent extension| as core credibility/debasement hedge)?[.]?",text.strip()):
                        reasons.append("Independent reader found an additional unmodeled invalidation/management requirement")
            targets = tuple(t.level for t in p.targets if t.level is not None)
            if f and sorted(targets) != sorted(f.targets):
                reasons.append("Target values disagree between independent reads")
            if any(t.level is None for t in p.targets):
                reasons.append("Non-scalar Crown target requires manual interpretation")
            if len(p.targets)>2 or any(t.level is not None and t.level<=0 for t in p.targets):
                reasons.append("Unsupported or invalid target ladder")
            for i,target in enumerate(p.targets):
                allowed_actions={"trim half","trim 50%","take half"} if i==0 else {"exit","close remainder","close all"}
                if target.action and target.action.lower().strip() not in allowed_actions:
                    reasons.append("Crown target action differs from the supported trim/exit policy")
            if list(targets)!=sorted(set(targets)):
                reasons.append("Target ladder is not strictly ascending; no silent reordering")
            if targets:
                deviations.append("Tier-1: fixed base-guide T1 half trim then break-even; next target closes remainder")
            if not thesis or not thesis.invalidation:
                deviations.append("No measurable Crown thesis-break test supplied; price protection and evidence timeout only")
            source=p.source.model_dump() | {"thesis": thesis.model_dump() if thesis else None}
            # Cross-instrument semantic thesis linking is not yet certified.
            # Treat all positions as one thesis for caps rather than undercounting
            # correlated exposure because local letter keys changed.
            plans.append(Plan(pid, "unresolved-research-thesis-cluster", symbol, at, expiry, x.letter_date, rhash,
                              p.position_key, source, D(anchor) if anchor else None,
                              D(multiplier), D(stop) if stop else None, kind, sessions or 1,
                              tuple(D(t) for t in sorted(targets)), tuple(deviations), not reasons,
                              tuple(dict.fromkeys(reasons)), tuple(invalidations)))
        batches.append(LetterBatch(at, x.letter_date, tuple(plans), tuple(closures), timing))
    return batches


def load_sources(cfg):
    from crown.letter.receipt import load_receipt
    sources, manifest, missing = [], [], []
    for pdf in letter_pdfs(cfg):
        rec, verified = load_cached(cfg, pdf), load_verification(pdf, cfg)
        if rec is None or verified is None:
            missing.append(pdf.name)
            continue
        receipt = load_receipt(cfg, rec.pdf_sha256)
        if receipt and not rec.received_at:
            # Preserve the cached extraction/correction chain. This copy binds
            # code-owned receipt evidence into the experiment's record hash.
            rec = rec.model_copy(update={"received_at": receipt["received_at"]})
        pages = len(PdfReader(pdf).pages)
        sources.append((rec, verified, pages))
        manifest.append({"pdf": pdf.name, "pdf_sha256": rec.pdf_sha256,
                         "record_sha256": record_hash(rec), "verification_sha256": digest(verified.model_dump()),
                         "received_at": rec.received_at, "receipt": receipt, "pages": pages,
                         "review_flags": [f.model_dump() for f in rec.extraction.review_flags]})
    if missing:
        raise RuntimeError("Corpus inventory incomplete; prepare missing reads first: " + ", ".join(missing))
    if not sources:
        raise RuntimeError("No cached source letters")
    return sources, sorted(manifest, key=lambda v: v["pdf"])
