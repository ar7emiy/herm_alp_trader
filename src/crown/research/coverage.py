"""Source-bound research backlog. Counts are weekly rows, not independent bets."""
from collections import Counter

from crown.letter.audit import record_hash
from crown.research.models import serial


REMEDIES = {
    "source": "Review the exact cited pages and reconcile critical facts; never remove an audit gate to create a trade.",
    "payoff": "Reconstruct the whole payoff and companion hedge. Test affordable expressions across price/volatility/time scenarios; correlation alone is insufficient.",
    "monitor": "Specify the reference, units, release/receipt time, threshold, freshness and missing-data exit. Narrative conditions still require review.",
    "trigger": "Implement the stated trigger using observations available before submission; do not infer a midpoint or trade a watch-only setup.",
    "entry": "Recover receipt and actual entry context; compare next executable prices and no-chase limits. Do not use Crown's old fill as ours.",
    "exit": "Verify stop convention and target actions. Compare risk overlays in shadow; never widen a published stop.",
    "restriction": "Respect Crown's no-add, closed and superseded states; these are intentional exclusions, not bugs to optimize away.",
    "other": "Inspect the source-bound reason before changing policy.",
}


def category(reason):
    text = reason.lower()
    if any(x in text for x in ("audit", "disagree", "source page", "coverage is incomplete")):
        return "source"
    if any(x in text for x in ("payoff", "hedge/compound")):
        return "payoff"
    if any(x in text for x in ("invalidation", "missing crown thesis")):
        return "monitor"
    if any(x in text for x in ("prohibits", "restriction", "non-executable")):
        return "restriction"
    if any(x in text for x in ("conditional", "trigger", "entry band")):
        return "trigger"
    if "anchor" in text:
        return "entry"
    if any(x in text for x in ("protective", "target")):
        return "exit"
    return "other"


def coverage_report(sources, batches, end):
    plans = {(p.record_sha256, p.crown_key): p for b in batches
             if b.available_at.date() <= end for p in b.plans}
    rows, counts, evidence = [], Counter(), Counter()
    for rec, _, _ in sources:
        rhash = record_hash(rec)
        for pos in rec.extraction.crown_positions:
            plan = plans.get((rhash, pos.position_key))
            if plan is None:
                continue
            thesis = next((t for t in rec.extraction.theses if t.thesis_key == pos.thesis_key), None)
            categories = sorted({category(r) for r in plan.reasons})
            counts.update(categories)
            if thesis:
                evidence.update({e.kind for e in thesis.evidence})
            rows.append({
                "letter_date": rec.extraction.letter_date, "pdf": rec.pdf_name,
                "pdf_sha256": rec.pdf_sha256, "record_sha256": rhash,
                "position_id": plan.position_id, "position_key": pos.position_key,
                "symbol": plan.symbol, "structure": pos.structure.type, "view": pos.view,
                "source": pos.source.model_dump(), "risk_units": pos.sizing_multiplier,
                "allowed": plan.allowed, "blockers": plan.reasons, "categories": categories,
                "deviations": plan.deviations, "available_at": plan.available_at,
                "thesis": thesis.model_dump() if thesis else None,
                "required_next_work": [REMEDIES[c] for c in categories],
                "interpretation": "Cached source extraction, potentially wrong; no inference of Crown's unpublished analytics.",
            })
    return serial({"row_count": len(rows), "eligible_rows": sum(r["allowed"] for r in rows),
                   "unique_eligible_episodes": len({r["position_id"] for r in rows if r["allowed"]}),
                   "blocker_counts": dict(counts.most_common()), "evidence_kinds_by_position_row": dict(evidence),
                   "counts_note": "Overlapping blockers; weekly observations repeat positions. No skipped P&L is invented.",
                   "rows": rows})
