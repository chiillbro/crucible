"""The agent that does nothing. A valid task must give it reward 0 every time."""

from __future__ import annotations

from crucible.deadline import Deadline
from crucible.domain.schema import AgentOutcome, Task
from crucible.ports.sandbox import Sandbox


class NopAgent:
    @property
    def name(self) -> str:
        return "nop"

    def solve(self, sandbox: Sandbox, task: Task, deadline: Deadline) -> AgentOutcome:  # noqa: ARG002
        return AgentOutcome(steps=0, stop_reason="noop", summary="made no changes")
