"""Agent port: something that acts inside a sandbox to satisfy an instruction."""

from __future__ import annotations

from typing import Protocol

from crucible.deadline import Deadline
from crucible.domain.schema import AgentOutcome, Task
from crucible.ports.sandbox import Sandbox


class Agent(Protocol):
    """Acts inside a running sandbox to satisfy ``task.instruction``.

    Agents receive the whole :class:`Task` because the oracle needs the reference
    solution. Every other agent must use only ``task.instruction``, ``task.workdir``
    and the sandbox; the hidden tests exist solely inside the verifier image.
    """

    @property
    def name(self) -> str: ...

    def solve(self, sandbox: Sandbox, task: Task, deadline: Deadline) -> AgentOutcome:
        """Work in ``sandbox`` until done or ``deadline`` passes.

        Raises:
            AgentError: the agent could not run or crashed.
            SandboxTimeoutError: a sandbox command exceeded its budget.
        """
        ...
