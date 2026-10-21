from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import pytest

from crown.letter.receipt import load_receipt, receive


def test_first_receipt_is_immutable_retry_does_not_backdate_or_overwrite(cfg, tmp_path):
    cfg = replace(cfg, backtest=replace(cfg.backtest, letters_dir=str(tmp_path/"private-letters")))
    incoming = tmp_path/"weekly.pdf"
    incoming.write_bytes(b"%PDF-1.4\nsynthetic test only")
    before = datetime.now(timezone.utc)
    pdf, path = receive(incoming, cfg)
    first = path.read_bytes()
    assert pdf.read_bytes() == incoming.read_bytes()
    stamp = datetime.fromisoformat(json.loads(first)["received_at"])
    assert stamp >= before
    assert receive(incoming, cfg) == (pdf, path)
    assert path.read_bytes() == first
    incoming.write_bytes(b"%PDF-1.4\ndifferent content")
    with pytest.raises(RuntimeError, match="different PDF"):
        receive(incoming, cfg)


def test_invalid_receipt_does_not_fallback_to_assumed_time(cfg):
    folder = cfg.paths.extracted_letters/"receipts"
    folder.mkdir(parents=True)
    (folder/f"{'a'*64}.json").write_text(json.dumps({"pdf_sha256": "a"*64,
        "method": "first_local_capture", "received_at": "2026-01-01T10:00:00"}))
    with pytest.raises(RuntimeError, match="Invalid hash-bound"):
        load_receipt(cfg, "a"*64)
