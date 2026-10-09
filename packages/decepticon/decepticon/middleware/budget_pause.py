"""Budget pause policy — graduated warnings and pause-on-limit.

Extends Decepticon's existing BudgetEnforcementMiddleware with:
- Graduated warnings at 70/85/95% of budget
- pause policy: park agents at limit instead of aborting
- extend_budget to resume after raising the limit
- Sub-agents stop at 90% reserving 10% for root wind-down
"""

from __future__ import annotations

import threading
from enum import StrEnum
from typing import Any


class BudgetPolicy(StrEnum):
    STOP = "stop"
    PAUSE = "pause"


WARNING_THRESHOLDS = [0.70, 0.85, 0.95]
SUBAGENT_CUTOFF = 0.90  # Sub-agents stop at 90%


class BudgetPauseManager:
    """Manages budget-aware execution with pause/resume capability."""

    def __init__(
        self,
        max_budget: float = 0.0,
        policy: BudgetPolicy = BudgetPolicy.STOP,
    ):
        self.max_budget = max_budget
        self.policy = policy
        self._spent = 0.0
        self._paused = False
        self._warnings_sent: set[float] = set()
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_budget - self._spent) if self.max_budget > 0 else float("inf")

    @property
    def utilization(self) -> float:
        return self._spent / self.max_budget if self.max_budget > 0 else 0.0

    @property
    def is_paused(self) -> bool:
        return self._paused

    def record_spend(self, amount: float) -> dict[str, Any]:
        """Record spending and return any warnings/actions."""
        with self._lock:
            self._spent += amount
            result: dict[str, Any] = {"spent": self._spent, "remaining": self.remaining}

            if self.max_budget <= 0:
                return result

            util = self.utilization

            # Check warning thresholds
            for threshold in WARNING_THRESHOLDS:
                if util >= threshold and threshold not in self._warnings_sent:
                    self._warnings_sent.add(threshold)
                    result["warning"] = (
                        f"Budget {int(threshold * 100)}% used ({self._spent:.2f}/{self.max_budget:.2f})"
                    )
                    result["warning_level"] = threshold

            # Check limit
            if util >= 1.0:
                if self.policy == BudgetPolicy.PAUSE:
                    self._paused = True
                    result["action"] = "paused"
                    result["message"] = (
                        "Budget limit reached. Execution paused. Use extend_budget() to resume."
                    )
                else:
                    result["action"] = "stopped"
                    result["message"] = "Budget limit reached. Execution stopped."

            # Sub-agent cutoff
            if util >= SUBAGENT_CUTOFF:
                result["subagent_cutoff"] = True
                result["subagent_message"] = (
                    "Sub-agents should stop. Reserve remaining budget for root wind-down."
                )

            return result

    def check_can_proceed(self, is_root: bool = False) -> dict[str, Any]:
        """Check if an agent can proceed with the next LLM call."""
        if self._paused:
            return {"can_proceed": False, "reason": "budget_paused", "policy": self.policy.value}
        if self.max_budget > 0:
            util = self.utilization
            if not is_root and util >= SUBAGENT_CUTOFF:
                return {
                    "can_proceed": False,
                    "reason": "subagent_budget_cutoff",
                    "utilization": util,
                }
            if util >= 1.0:
                return {"can_proceed": False, "reason": "budget_exceeded", "utilization": util}
        return {"can_proceed": True, "utilization": self.utilization}

    def extend_budget(self, additional: float) -> dict[str, Any]:
        """Extend the budget and resume if paused."""
        with self._lock:
            self.max_budget += additional
            was_paused = self._paused
            self._paused = False
            return {
                "extended": True,
                "new_max": self.max_budget,
                "spent": self._spent,
                "remaining": self.remaining,
                "resumed": was_paused,
            }

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def status(self) -> dict[str, Any]:
        return {
            "max_budget": self.max_budget,
            "spent": self._spent,
            "remaining": self.remaining,
            "utilization": round(self.utilization, 4),
            "policy": self.policy.value,
            "paused": self._paused,
            "warnings_sent": sorted(self._warnings_sent),
        }
