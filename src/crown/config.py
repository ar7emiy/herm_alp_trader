"""Load explicit configuration and optional local credentials.

See docs/05-portfolio-and-risk.md. Offline tooling must use load_env=False.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SizingConfig:
    sleeve_override: float | None
    investable_assets: float | None
    tactical_sleeve_pct: float
    risk_per_1_00_position: float

    @property
    def sleeve(self) -> float:
        if self.sleeve_override is not None:
            return self.sleeve_override
        if self.investable_assets is None:
            raise ValueError(
                "config.yaml sizing: neither sleeve_override nor "
                "investable_assets is set"
            )
        return self.investable_assets * self.tactical_sleeve_pct

    @property
    def risk_at_1_00(self) -> float:
        return self.sleeve * self.risk_per_1_00_position


@dataclass(frozen=True)
class GuardrailsConfig:
    max_order_notional: float
    max_concurrent_positions: int
    daily_loss_limit: float
    allowed_order_types: list[str]
    halt_file: str


@dataclass(frozen=True)
class NoChaseConfig:
    max_pct_from_trigger: float
    default_watch_days: int


@dataclass(frozen=True)
class AlternativeFinderConfig:
    min_edge_ratio: float
    min_days_to_expiry: int
    default_proxy_stop_pct: float
    missing_multiplier_default: float


@dataclass(frozen=True)
class ScheduleTier:
    max_distance: float | None
    cadence_minutes: int


@dataclass(frozen=True)
class SchedulingConfig:
    tiers: list[ScheduleTier]
    max_concurrent_tight_jobs: int

    def cadence_for_distance(self, distance: float) -> int:
        """distance is fractional (0.05 = 5%). Tiers are checked in order;
        the first tier whose max_distance >= distance wins. A tier with
        max_distance=None is the fallback (base/hourly) tier."""
        applicable = [t for t in self.tiers if t.max_distance is not None]
        applicable.sort(key=lambda t: t.max_distance)
        for tier in applicable:
            if distance <= tier.max_distance:
                return tier.cadence_minutes
        base = next(t for t in self.tiers if t.max_distance is None)
        return base.cadence_minutes


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str
    end_date: str | None
    letters_dir: str
    data_dir: str
    approval_latency_minutes: int
    approval_rate: float


@dataclass(frozen=True)
class PathsConfig:
    parsed_letters: Path
    plans: Path
    bars: Path
    trade_log_db: Path
    reports: Path
    extracted_letters: Path
    probe: Path


@dataclass(frozen=True)
class ModelConfig:
    id: str


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    extract_model: str
    extract_reasoning_effort: str | None  # null for non-reasoning models
    extract_max_output_tokens: int
    extractor_version: str
    extract_pdf_detail: str = "high"

    def __post_init__(self):
        if self.extract_pdf_detail not in {"low", "high", "auto"}:
            raise ValueError("llm.extract_pdf_detail must be low, high, or auto")


@dataclass(frozen=True)
class CapabilitiesConfig:
    account_type: str
    verified: bool
    options_level: int
    allow_debit_spreads: bool
    allow_long_options: bool
    allow_fractional: bool


@dataclass(frozen=True)
class Config:
    sizing: SizingConfig
    guardrails: GuardrailsConfig
    no_chase: NoChaseConfig
    alternative_finder: AlternativeFinderConfig
    scheduling: SchedulingConfig
    backtest: BacktestConfig
    paths: PathsConfig
    model: ModelConfig
    llm: LLMConfig
    capabilities: CapabilitiesConfig


def load_config(path: str | Path | None = None, *, load_env: bool = True) -> Config:
    """Read configuration; offline tools/tests can explicitly avoid credential files."""
    if load_env:
        load_dotenv(REPO_ROOT / ".env")

    config_path = Path(path) if path else REPO_ROOT / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    def resolve(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else REPO_ROOT / pp

    return Config(
        sizing=SizingConfig(**raw["sizing"]),
        guardrails=GuardrailsConfig(**raw["guardrails"]),
        no_chase=NoChaseConfig(**raw["no_chase"]),
        alternative_finder=AlternativeFinderConfig(**raw["alternative_finder"]),
        scheduling=SchedulingConfig(
            tiers=[ScheduleTier(**t) for t in raw["scheduling"]["tiers"]],
            max_concurrent_tight_jobs=raw["scheduling"]["max_concurrent_tight_jobs"],
        ),
        backtest=BacktestConfig(**raw["backtest"]),
        paths=PathsConfig(
            parsed_letters=resolve(raw["paths"]["parsed_letters"]),
            plans=resolve(raw["paths"]["plans"]),
            bars=resolve(raw["paths"]["bars"]),
            trade_log_db=resolve(raw["paths"]["trade_log_db"]),
            reports=resolve(raw["paths"]["reports"]),
            extracted_letters=resolve(raw["paths"]["extracted_letters"]),
            probe=resolve(raw["paths"]["probe"]),
        ),
        model=ModelConfig(**raw["model"]),
        llm=LLMConfig(**raw["llm"]),
        capabilities=CapabilitiesConfig(**raw["capabilities"]),
    )


def openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set (check .env)")
    return key


def alpaca_is_paper() -> bool:
    """Defaults to paper. Only an explicit ALPACA_PAPER=false selects live."""
    return os.environ.get("ALPACA_PAPER", "true").strip().lower() not in ("false", "0", "no")


def anthropic_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set (check .env)")
    return key


def alpaca_credentials() -> tuple[str, str]:
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set (check .env)")
    return key, secret
