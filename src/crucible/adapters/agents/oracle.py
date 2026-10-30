"""The agent that applies the reference solution. A valid task must give it reward 1."""

from __future__ import annotations

import logging

from crucible.deadline import Deadline
from crucible.domain.schema import AgentOutcome, Task
from crucible.errors import AgentError
from crucible.ports.sandbox import Sandbox

log = logging.getLogger(__name__)

SOLUTION_DIR = "/solution"
_TAIL_CHARS = 2000


class OracleAgent:
    """Copies ``task.solution_dir`` into the sandbox and runs ``solve.sh`` with ``sh``.

    The script runs in the task workdir. Only changes inside the workdir are graded, so a
    reference solution must confine itself to it.
    """

    @property
    def name(self) -> str:
        return "oracle"

    def solve(self, sandbox: Sandbox, task: Task, deadline: Deadline) -> AgentOutcome:
        sandbox.copy_in(task.solution_dir, SOLUTION_DIR)
        result = sandbox.exec(
            f"sh {SOLUTION_DIR}/solve.sh",
            timeout=deadline.remaining(),
            workdir=task.workdir,
        )
        if not result.ok:
            msg = (
                f"reference solution exited with {result.exit_code}:\n"
                f"{result.stderr[-_TAIL_CHARS:] or result.stdout[-_TAIL_CHARS:]}"
            )
            raise AgentError(msg)
        log.debug("oracle applied solution in %.1fs", result.duration_seconds)
        return AgentOutcome(
            steps=1, stop_reason="done", summary=result.stdout[-_TAIL_CHARS:] or None
        )
