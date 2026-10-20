"""Bounded extraction + independent corroboration for a research corpus."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from crown.config import Config
from crown.diagnostics import error_label, safe_error
from crown.letter.audit import audit_record, latest_page_review, record_hash, write_audit
from crown.letter.extract import extract_letter, letter_pdfs, load_cached
from crown.letter.verify import corroboration, verify_letter, load_verification


def prepare_one(pdf: Path, cfg: Config, offline: bool = False) -> dict:
    record = load_cached(cfg,pdf) if offline else extract_letter(pdf,cfg)
    independent = load_verification(pdf,cfg) if offline else verify_letter(pdf,cfg)
    if record is None or independent is None:
        raise RuntimeError("Missing cached source read in offline preparation")
    count = len(PdfReader(pdf).pages)
    audit = audit_record(record, count, latest_page_review(record, cfg.paths.reports))
    write_audit(audit, cfg.paths.reports)
    report = corroboration(record, independent, count)
    report["extraction_sha256"] = record_hash(record)
    report["verification_sha256"] = hashlib.sha256(independent.model_dump_json().encode()).hexdigest()
    out = cfg.paths.reports / "corroboration" / (pdf.stem + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"file": pdf.name, "letter_date": record.extraction.letter_date,
            "positions": len(record.extraction.crown_positions),
            "plain_long_agreement": sum(p["agrees"] for p in report["positions"]),
            "plain_long_rows": len(report["positions"]),
            "audit_errors": sum(i.severity == "error" for i in audit.issues),
            "status": "prepared_for_research_review"}


def prepare_corpus(cfg: Config, workers: int = 4, progress=print, offline=False) -> dict:
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(prepare_one, pdf, cfg, offline): pdf for pdf in letter_pdfs(cfg)}
        for job in as_completed(jobs):
            try:
                result = job.result()
            except Exception as exc:
                result = {"file": jobs[job].name, "status": "failed", "error": error_label(safe_error(exc))}
            results.append(result)
            progress(json.dumps(result, ensure_ascii=True))
    report = {"at": datetime.now(timezone.utc).isoformat(), "orders_enabled": False,
              "results": sorted(results, key=lambda r: r.get("letter_date", r["file"]))}
    out = cfg.paths.reports / "corpus-preparation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
