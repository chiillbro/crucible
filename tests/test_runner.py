"""Runner orchestration tests with fake ports. No Docker involved."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from crucible.deadline import Deadline
from crucible.domain.schema import AgentOutcome, Task, TrialStatus, load_task
from crucible.errors import (
    AgentError,
    BuildError,
    SandboxError,
    SandboxTimeoutError,
    VerifierError,
)
from crucible.ports import Sandbox
from crucible.runner import Runner, dump_result
from tests.conftest import MINIMAL_TASK_TOML, TaskDirFactory
from tests.fakes import FakeBackend, FakeSandbox, FakeVerifier, ScriptedAgent, failed, passed


@pytest.fixture
def task(make_task_dir: TaskDirFactory) -> Task:
    return load_task(make_task_dir())


def test_happy_path_builds_prepares_runs_trials_and_scores(task: Task, tmp_path: Path) -> None:
    backend = FakeBackend()
    verifier = FakeVerifier(verdicts=[passed(), passed(), failed()])

    def solve(sandbox: Sandbox, _task: Task, deadline: Deadline) -> AgentOutcome:
        assert isinstance(sandbox, FakeSandbox)
        assert sandbox.workdir == "/workspace"
        assert 0 < deadline.remaining() <= 900
        sandbox.write_file("/workspace/fix.py", b"ok")
        return AgentOutcome(steps=3, summary="done")

    agent = ScriptedAgent(solve, agent_name="fake")
    runner = Runner(backend=backend, agent=agent, verifier=verifier, artifacts_dir=tmp_path / "art")
    result = runner.run(task, trials=3, k=2)

    # build + prepare
    context, tag, _, timeout = backend.builds[0]
    assert context == task.environment_dir
    assert tag.startswith("crucible/demo-task-env:")
    assert timeout == task.config.environment.build_timeout_seconds
    assert verifier.prepared[0][1].tag == tag

    # trials
    assert agent.calls == 3
    assert len(backend.starts) == 3
    assert all(sb.closed for sb in backend.sandboxes)
    assert len(verifier.verified) == 3
    assert verifier.verified[0].root == "/workspace"
    assert [t.status for t in result.trials] == [
        TrialStatus.PASSED,
        TrialStatus.PASSED,
        TrialStatus.FAILED,
    ]
    assert [t.reward for t in result.trials] == [1, 1, 0]
    assert result.trials[0].agent == AgentOutcome(steps=3, summary="done")
    assert result.trials[0].reason is None
    assert result.trials[2].reason == "hidden tests failed (pytest exit 1)"

    # score and metadata
    assert result.score.n == 3
    assert result.score.c == 2
    assert result.score.pass_at_1 == pytest.approx(2 / 3)
    assert result.score.pass_at_k == 1.0
    assert result.agent == "fake"
    assert result.task_name == "demo-task"
    assert result.images == {"env": tag, "crucible/fake-verifier": "crucible/fake-verifier:1"}
    assert result.finished_at >= result.started_at
    assert backend.removed == []

    # artifacts
    trial_json = json.loads((tmp_path / "art" / "trial-02" / "trial.json").read_text())
    assert trial_json["status"] == "failed"
    assert (tmp_path / "art" / "trial-02" / "verifier.txt").read_text() == "1 failed"


def test_agent_error_is_recorded_and_run_continues(task: Task) -> None:
    calls = {"n": 0}

    def solve(_sb: Sandbox, _task: Task, _deadline: Deadline) -> AgentOutcome:
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "reference solution exited with 2"
            raise AgentError(msg)
        return AgentOutcome()

    verifier = FakeVerifier()
    result = Runner(backend=FakeBackend(), agent=ScriptedAgent(solve), verifier=verifier).run(
        task, trials=2, k=1
    )
    first, second = result.trials
    assert first.status is TrialStatus.AGENT_ERROR
    assert first.reward == 0
    assert first.reason == "reference solution exited with 2"
    assert first.verdict is None
    assert second.status is TrialStatus.PASSED
    assert len(verifier.verified) == 1
    assert result.score.pass_at_1 == 0.5


def test_unexpected_agent_exception_is_an_agent_error(task: Task) -> None:
    def solve(_sb: Sandbox, _task: Task, _deadline: Deadline) -> AgentOutcome:
        msg = "bug"
        raise KeyError(msg)

    result = Runner(backend=FakeBackend(), agent=ScriptedAgent(solve), verifier=FakeVerifier()).run(
        task, trials=1, k=1
    )
    trial = result.trials[0]
    assert trial.status is TrialStatus.AGENT_ERROR
    assert trial.reason == "agent crashed: KeyError('bug')"


def test_sandbox_timeout_inside_agent_is_an_agent_timeout(task: Task) -> None:
    def solve(_sb: Sandbox, _task: Task, _deadline: Deadline) -> AgentOutcome:
        msg = "command exceeded 5s"
        raise SandboxTimeoutError(msg)

    result = Runner(backend=FakeBackend(), agent=ScriptedAgent(solve), verifier=FakeVerifier()).run(
        task, trials=1, k=1
    )
    assert result.trials[0].status is TrialStatus.AGENT_TIMEOUT
    assert result.trials[0].reason == "command exceeded 5s"


def test_hanging_agent_is_killed_at_the_budget(make_task_dir: TaskDirFactory) -> None:
    toml = MINIMAL_TASK_TOML + "\n[agent]\ntimeout_seconds = 0.3\n"
    task = load_task(make_task_dir(toml=toml))
    released = threading.Event()

    def solve(sandbox: Sandbox, _task: Task, deadline: Deadline) -> AgentOutcome:
        assert isinstance(sandbox, FakeSandbox)
        assert deadline.remaining() <= 0.3
        while not sandbox.killed:  # a real agent blocks in exec(); the kill unblocks it
            time.sleep(0.01)
        released.set()
        sandbox.exec("true", timeout=1)  # raises: the sandbox is dead
        return AgentOutcome()

    backend = FakeBackend()
    verifier = FakeVerifier()
    started = time.monotonic()
    result = Runner(backend=backend, agent=ScriptedAgent(solve), verifier=verifier).run(
        task, trials=1, k=1
    )
    elapsed = time.monotonic() - started
    assert released.wait(2)
    trial = result.trials[0]
    assert trial.status is TrialStatus.AGENT_TIMEOUT
    assert trial.reward == 0
    assert trial.reason == "agent exceeded its 0s budget; sandbox killed"
    assert backend.sandboxes[0].killed
    assert backend.sandboxes[0].closed
    assert elapsed < 2
    assert verifier.verified == []


def test_sandbox_start_failure_is_recorded(task: Task) -> None:
    backend = FakeBackend(start_error=SandboxError("could not create container"))
    agent = ScriptedAgent()
    result = Runner(backend=backend, agent=agent, verifier=FakeVerifier()).run(task, trials=2, k=1)
    assert [t.status for t in result.trials] == [TrialStatus.SANDBOX_ERROR] * 2
    assert result.trials[0].reason == "could not create container"
    assert agent.calls == 0
    assert result.score.mean_reward == 0.0


def test_verifier_error_keeps_agent_outcome(task: Task) -> None:
    verifier = FakeVerifier(
        verdicts=[VerifierError("no tests collected"), SandboxError("daemon gone")]
    )
    agent = ScriptedAgent(lambda _sb, _t, _d: AgentOutcome(steps=7))
    result = Runner(backend=FakeBackend(), agent=agent, verifier=verifier).run(task, trials=2, k=1)
    for trial, reason in zip(result.trials, ["no tests collected", "daemon gone"], strict=True):
        assert trial.status is TrialStatus.VERIFIER_ERROR
        assert trial.reward == 0
        assert trial.reason == reason
        assert trial.agent == AgentOutcome(steps=7)
        assert trial.verdict is None


def test_verifier_crash_is_recorded(task: Task) -> None:
    verifier = FakeVerifier(verdicts=[RuntimeError("bug")])
    result = Runner(backend=FakeBackend(), agent=ScriptedAgent(), verifier=verifier).run(
        task, trials=1, k=1
    )
    assert result.trials[0].status is TrialStatus.VERIFIER_ERROR
    assert result.trials[0].reason == "verifier crashed: RuntimeError('bug')"


def test_verifier_timeout_verdict_reason_is_its_output(task: Task) -> None:
    from crucible.domain.schema import Verdict

    timed_out = Verdict(
        passed=False, exit_code=None, output="verifier timed out after 300s", duration_seconds=300
    )
    verifier = FakeVerifier(verdicts=[timed_out])
    result = Runner(backend=FakeBackend(), agent=ScriptedAgent(), verifier=verifier).run(
        task, trials=1, k=1
    )
    assert result.trials[0].status is TrialStatus.FAILED
    assert result.trials[0].reason == "verifier timed out after 300s"


def test_build_failure_propagates_before_any_trial(task: Task) -> None:
    backend = FakeBackend(build_error=BuildError("step 3 failed"))
    agent = ScriptedAgent()
    with pytest.raises(BuildError, match="step 3 failed"):
        Runner(backend=backend, agent=agent, verifier=FakeVerifier()).run(task, trials=2, k=1)
    assert agent.calls == 0


def test_remove_images_cleans_up_even_when_trials_fail(task: Task) -> None:
    backend = FakeBackend(start_error=SandboxError("nope"))
    runner = Runner(
        backend=backend, agent=ScriptedAgent(), verifier=FakeVerifier(), remove_images=True
    )
    runner.run(task, trials=1, k=1)
    assert [image.tag for image in backend.removed] == [
        "crucible/fake-verifier:1",
        backend.builds[0][1],
    ]


def test_remove_images_after_verifier_build_failure(task: Task) -> None:
    class ExplodingVerifier(FakeVerifier):
        def prepare(self, task: Task, environment: object) -> tuple[()]:
            msg = "tests image failed"
            raise BuildError(msg)

    backend = FakeBackend()
    runner = Runner(
        backend=backend, agent=ScriptedAgent(), verifier=ExplodingVerifier(), remove_images=True
    )
    with pytest.raises(BuildError):
        runner.run(task, trials=1, k=1)
    assert [image.tag for image in backend.removed] == [backend.builds[0][1]]


@pytest.mark.parametrize(("trials", "k"), [(0, 1), (1, 0)])
def test_rejects_non_positive_trials_or_k(task: Task, trials: int, k: int) -> None:
    runner = Runner(backend=FakeBackend(), agent=ScriptedAgent(), verifier=FakeVerifier())
    with pytest.raises(ValueError, match="at least 1"):
        runner.run(task, trials=trials, k=k)


def test_dump_result_writes_json(task: Task, tmp_path: Path) -> None:
    result = Runner(backend=FakeBackend(), agent=ScriptedAgent(), verifier=FakeVerifier()).run(
        task, trials=1, k=1
    )
    path = tmp_path / "nested" / "results.json"
    dump_result(result, path)
    data = json.loads(path.read_text())
    assert data["task_name"] == "demo-task"
    assert data["score"]["pass_at_k"] == 1.0
    assert data["trials"][0]["status"] == "passed"
