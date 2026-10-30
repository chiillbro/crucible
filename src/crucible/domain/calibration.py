"""Calibration: a task is valid only if the oracle always passes and nop always fails."""

from __future__ import annotations

from dataclasses import dataclass

from crucible.domain.schema import TaskResult


@dataclass(frozen=True)
class Calibration:
    oracle: TaskResult
    nop: TaskResult

    @property
    def ok(self) -> bool:
        return not self.problems()

    def problems(self) -> list[str]:
        """Human-readable reasons the task is not calibrated; empty when it is."""
        problems: list[str] = []
        oracle_failures = [t for t in self.oracle.trials if t.reward != 1]
        nop_passes = [t for t in self.nop.trials if t.reward != 0]
        if not self.oracle.trials:
            problems.append("oracle ran no trials")
        if not self.nop.trials:
            problems.append("nop ran no trials")
        for trial in oracle_failures:
            problems.append(
                f"oracle trial {trial.index} scored 0 ({trial.status.value}): "
                f"{trial.reason or 'hidden tests failed'}"
            )
        for trial in nop_passes:
            problems.append(
                f"nop trial {trial.index} scored 1: hidden tests pass without any change"
            )
        return problems
