"""Capture first local receipt now, never backdate a historical PDF."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def receipt_path(cfg, pdf_hash):
    return cfg.paths.extracted_letters/"receipts"/f"{pdf_hash}.json"


def receive(pdf, cfg):
    pdf = Path(pdf)
    if pdf.suffix.lower() != ".pdf":
        raise RuntimeError("Letter receipt requires a PDF")
    content = pdf.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise RuntimeError("Letter is not a PDF")
    sha = hashlib.sha256(content).hexdigest()
    letters = Path(cfg.backtest.letters_dir)
    if not letters.is_absolute():
        from crown.config import REPO_ROOT
        letters = REPO_ROOT/letters
    destination = letters/pdf.name
    letters.mkdir(parents=True, exist_ok=True)
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
        raise RuntimeError("A different PDF already has this name; no overwrite permitted")
    path = receipt_path(cfg, sha)
    if path.exists():
        load_receipt(cfg, sha)  # Validate an existing immutable receipt.
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"pdf_sha256": sha, "received_at": datetime.now(timezone.utc).isoformat(),
                   "method": "first_local_capture", "note": "Actual local capture, not inferred publication or email delivery time"}
        # Exclusive create makes simultaneous retries unable to backdate receipt.
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, indent=2)
        except FileExistsError:
            load_receipt(cfg, sha)
    if not destination.exists():
        with destination.open("xb") as stream:
            stream.write(content)
    return destination, path


def load_receipt(cfg, sha):
    path = receipt_path(cfg, sha)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(value["received_at"])
        if value["pdf_sha256"] != sha or value["method"] != "first_local_capture" or at.tzinfo is None:
            raise ValueError("Invalid receipt")
        if at > datetime.now(timezone.utc):
            raise ValueError("Future receipt")
        return value
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("Invalid hash-bound local receipt; no timing assumption substituted") from None
