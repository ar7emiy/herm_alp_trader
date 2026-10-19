"""Audited source corrections to extraction data. No order or policy mutations."""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from crown.config import Config
from crown.letter.audit import record_hash
from crown.letter.extract import _archive, cache_path, load_cached
from crown.letter.schema import ExtractionRecord, LetterExtraction, Source


class CorrectionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    before: Any
    after: Any
    source: Source
    reason: str = Field(min_length=1)


class CorrectionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extraction_sha256: str
    reviewer: str = Field(min_length=1)
    changes: list[CorrectionEdit] = Field(min_length=1)


def apply_corrections(pdf_path: Path, batch: CorrectionBatch, cfg: Config) -> ExtractionRecord:
    record = load_cached(cfg, pdf_path)
    if record is None or record_hash(record) != batch.extraction_sha256:
        raise ValueError("Correction does not match the active extraction")
    page_count = len(PdfReader(pdf_path).pages)
    data = copy.deepcopy(record.extraction.model_dump())
    for edit in batch.changes:
        if edit.source.page is None or not 1 <= edit.source.page <= page_count or not edit.source.quote.strip():
            raise ValueError("Correction requires a valid source page and quote")
        if not edit.path.startswith("/") or edit.path == "/":
            raise ValueError("Correction requires a non-root JSON pointer within extraction")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in edit.path[1:].split("/")]
        parent = data
        try:
            for part in parts[:-1]:
                if isinstance(parent, list):
                    if not part.isdecimal():
                        raise ValueError("Invalid array index")
                    parent = parent[int(part)]
                else:
                    parent = parent[part]
            if isinstance(parent, list) and not parts[-1].isdecimal():
                raise ValueError("Invalid array index")
            key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
            if isinstance(key, int) and key < 0:
                raise ValueError("Negative array index")
            if parent[key] != edit.before:
                raise ValueError("Correction's before value no longer matches")
            parent[key] = edit.after
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Correction path does not exist") from exc
    corrected = LetterExtraction.model_validate(data)
    updated = record.model_copy(update={
        "extraction": corrected,
        "corrections": [*record.corrections, {
            "at": datetime.now(timezone.utc).isoformat(), "reviewer": batch.reviewer,
            "base_record_sha256": batch.extraction_sha256,
            "batch_sha256": hashlib.sha256(batch.model_dump_json().encode()).hexdigest(),
            "changes": [edit.model_dump() for edit in batch.changes],
        }],
    })
    out = cache_path(cfg, pdf_path)
    history = out.parent / "history" / out.stem
    _archive(out.read_text(encoding="utf-8"), history)
    content = updated.model_dump_json(indent=2)
    _archive(content, history)
    temp = out.with_suffix(".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(out)
    return updated
