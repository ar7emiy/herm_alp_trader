from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from crown.config import load_config


@pytest.fixture(autouse=True)
def forbid_credential_file_loading(monkeypatch):
    """Offline tests must never load a developer's real .env, even accidentally."""
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must use load_config(load_env=False)")

    monkeypatch.setattr("crown.config.load_dotenv", forbidden)


@pytest.fixture
def cfg(tmp_path: Path):
    """Real config.yaml values, but with every filesystem path (HALT file,
    trade log, caches) redirected under pytest's tmp_path so no test run can
    ever touch the real repo's data/ directory."""
    base = load_config(load_env=False)
    return dataclasses.replace(
        base,
        guardrails=dataclasses.replace(base.guardrails, halt_file=str(tmp_path / "HALT")),
        paths=dataclasses.replace(
            base.paths,
            parsed_letters=tmp_path / "parsed",
            plans=tmp_path / "plans",
            bars=tmp_path / "bars",
            trade_log_db=tmp_path / "logs" / "trades.db",
            reports=tmp_path / "reports",
            extracted_letters=tmp_path / "extracted",
            probe=tmp_path / "probe",
        ),
    )
