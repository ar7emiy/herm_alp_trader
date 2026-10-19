"""Approval is a hard rule (04-system-prompt.md: "never place an order
requiring approval without receiving it"), and in the backtest a human isn't
available to approve staged trades in real time. We simulate the gate with a
policy — a fixed delay and an approval probability — rather than skipping
it, so the replay's timing (fills happen `approval_latency_minutes` after a
trigger, not instantly) and the "what if some staged trades get rejected"
sensitivity are both visible in the report, not silently assumed away."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ApprovalDecision:
    approved: bool
    decided_at: datetime


class AutoApprovalPolicy:
    def __init__(self, latency_minutes: int, approve_rate: float, seed: int = 42):
        self.latency = timedelta(minutes=latency_minutes)
        self.approve_rate = approve_rate
        self._rng = random.Random(seed)

    def decide(self, triggered_at: datetime) -> ApprovalDecision:
        approved = self._rng.random() < self.approve_rate
        return ApprovalDecision(approved=approved, decided_at=triggered_at + self.latency)
