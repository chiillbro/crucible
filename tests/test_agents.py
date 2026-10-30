"""Oracle and nop agent tests against the fake sandbox."""

from __future__ import annotations

import pytest

from crucible.adapters.agents import NopAgent, OracleAgent
from crucible.deadline import Deadline
from crucible.domain.schema import load_task
from crucible.errors import AgentError
from tests.conftest import TaskDirFactory
from tests.fakes import FakeSandbox, fail, ok


def test_nop_touches_nothing(make_task_dir: TaskDirFactory) -> None:
    task = load_task(make_task_dir())
    sandbox = FakeSandbox()
    outcome = NopAgent().solve(sandbox, task, Deadline.after(10))
    assert NopAgent().name == "nop"
    assert outcome.steps == 0
    assert sandbox.commands == []
    assert sandbox.copied == []
    assert sandbox.files == {}


def test_oracle_copies_solution_and_runs_it_in_workdir(make_task_dir: TaskDirFactory) -> None:
    task = load_task(make_task_dir())
    sandbox = FakeSandbox(handler=lambda _cmd: ok(stdout="applied\n"))
    outcome = OracleAgent().solve(sandbox, task, Deadline.after(100))
    assert OracleAgent().name == "oracle"
    assert sandbox.copied == [(task.solution_dir, "/solution")]
    command, timeout, workdir = sandbox.commands[0]
    assert command == "sh /solution/solve.sh"
    assert 99 < timeout <= 100
    assert workdir == "/workspace"
    assert outcome.steps == 1
    assert outcome.summary == "applied\n"


def test_oracle_failure_is_an_agent_error(make_task_dir: TaskDirFactory) -> None:
    task = load_task(make_task_dir())
    sandbox = FakeSandbox(handler=lambda _cmd: fail(exit_code=2, stderr="patch failed"))
    with pytest.raises(AgentError, match=r"exited with 2(.|\n)*patch failed"):
        OracleAgent().solve(sandbox, task, Deadline.after(100))
