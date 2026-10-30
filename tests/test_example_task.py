"""End-to-end: the shipped example task is calibrated and leak-free. Needs Docker."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from crucible import cli
from crucible.adapters.agents import NopAgent, OracleAgent
from crucible.adapters.docker_sandbox import DockerSandboxBackend
from crucible.adapters.pytest_verifier import PytestVerifier
from crucible.deadline import Deadline
from crucible.domain.calibration import Calibration
from crucible.domain.schema import AgentOutcome, Task, TrialStatus, load_task
from crucible.ports import Sandbox
from crucible.runner import Runner
from tests.fakes import ScriptedAgent

pytestmark = pytest.mark.docker

EXAMPLE_TASK = Path(__file__).resolve().parents[1] / "tasks" / "fix-config-parser"


@pytest.fixture(scope="module")
def backend() -> DockerSandboxBackend:
    return DockerSandboxBackend()


@pytest.fixture(scope="module")
def task() -> Task:
    return load_task(EXAMPLE_TASK)


def test_oracle_passes_and_nop_fails(
    backend: DockerSandboxBackend, task: Task, tmp_path: Path
) -> None:
    oracle = Runner(
        backend=backend,
        agent=OracleAgent(),
        verifier=PytestVerifier(backend),
        artifacts_dir=tmp_path / "oracle",
    ).run(task, trials=2, k=2)
    nop = Runner(backend=backend, agent=NopAgent(), verifier=PytestVerifier(backend)).run(
        task, trials=1, k=1
    )

    assert [t.status for t in oracle.trials] == [TrialStatus.PASSED, TrialStatus.PASSED]
    assert oracle.score.pass_at_k == 1.0
    assert oracle.score.pass_at_1 == 1.0
    assert "19 passed" in (tmp_path / "oracle" / "trial-01" / "verifier.txt").read_text()

    assert nop.trials[0].status is TrialStatus.FAILED
    assert nop.score.pass_at_1 == 0.0
    assert nop.trials[0].verdict is not None
    assert "7 failed, 12 passed" in nop.trials[0].verdict.output

    assert Calibration(oracle=oracle, nop=nop).ok
    assert set(oracle.images) == {"env", "verifier-base", "verifier"}


def test_agent_sandbox_has_no_hidden_tests_network_or_root(
    backend: DockerSandboxBackend, task: Task
) -> None:
    seen: dict[str, str] = {}

    def probe(sandbox: Sandbox, _task: Task, _deadline: Deadline) -> AgentOutcome:
        seen["hidden"] = sandbox.exec(
            "find / -name 'test_hidden.py' -not -path '/proc/*' 2>/dev/null | wc -l", timeout=60
        ).stdout.strip()
        seen["visible"] = str(sandbox.exec("python -m pytest -q", timeout=120).exit_code)
        seen["uid"] = sandbox.exec("id -u", timeout=30).stdout.strip()
        fetch = "import urllib.request; urllib.request.urlopen('http://1.1.1.1', timeout=3)"
        seen["net"] = str(sandbox.exec(f'python -c "{fetch}"', timeout=30).exit_code)
        return AgentOutcome(steps=4)

    result = Runner(
        backend=backend,
        agent=ScriptedAgent(probe, agent_name="probe"),
        verifier=PytestVerifier(backend),
    ).run(task, trials=1, k=1)
    assert seen == {"hidden": "0", "visible": "0", "uid": "1000", "net": "1"}
    assert result.trials[0].status is TrialStatus.FAILED  # probing is not solving


def test_validate_command_end_to_end(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["validate", str(EXAMPLE_TASK), "--trials", "1", "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "oracle: 1/1 passed" in result.output
    assert "nop:    0/1 passed" in result.output
    assert "result: calibrated" in result.output
