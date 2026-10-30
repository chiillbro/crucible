"""CLI tests. Docker is replaced by fakes through the factory hooks in ``crucible.cli``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from crucible import __version__, cli
from crucible.adapters.agents import LlmAgent
from crucible.adapters.pytest_verifier import PytestVerifier
from crucible.config import Settings
from crucible.domain.schema import Verdict
from crucible.errors import BuildError, ConfigError, SandboxError
from crucible.ports import SandboxBackend
from tests.conftest import TaskDirFactory
from tests.fakes import FakeBackend, FakeVerifier, failed, passed

runner = CliRunner()


@pytest.fixture
def fake_ports(monkeypatch: pytest.MonkeyPatch) -> FakeVerifier:
    verifier = FakeVerifier()
    monkeypatch.setattr(cli, "make_backend", lambda **_: FakeBackend())
    monkeypatch.setattr(cli, "make_verifier", lambda _backend: verifier)
    return verifier


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"crucible {__version__}"


def test_no_args_shows_help() -> None:
    result = runner.invoke(cli.app, [])
    assert "Usage" in result.output


def test_run_rejects_bad_task_dir(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["run", str(tmp_path / "missing")])
    assert result.exit_code == cli.EXIT_USAGE
    assert "error: task directory not found" in result.output


def test_run_writes_results_and_summary(
    make_task_dir: TaskDirFactory, fake_ports: FakeVerifier, tmp_path: Path
) -> None:
    fake_ports.verdicts = [passed(), failed()]
    out = tmp_path / "runs"
    result = runner.invoke(
        cli.app,
        [
            "run",
            str(make_task_dir()),
            "--agent",
            "nop",
            "--trials",
            "2",
            "--k",
            "2",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "agent:  nop" in result.output
    assert "trials: 2  passed: 1" in result.output
    results_files = list(out.glob("demo-task/nop/*/results.json"))
    assert len(results_files) == 1
    data = json.loads(results_files[0].read_text())
    assert data["agent"] == "nop"
    assert [t["reward"] for t in data["trials"]] == [1, 0]
    assert (results_files[0].parent / "trial-01" / "verifier.txt").exists()
    assert f"results: {results_files[0]}" in result.output


def test_run_reports_crucible_errors(
    make_task_dir: TaskDirFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "make_backend", lambda **_: FakeBackend(build_error=BuildError("step failed"))
    )
    monkeypatch.setattr(cli, "make_verifier", lambda _b: FakeVerifier())
    result = runner.invoke(cli.app, ["run", str(make_task_dir())])
    assert result.exit_code == cli.EXIT_ERROR
    assert "error: step failed" in result.output


def test_run_reports_unreachable_backend(
    make_task_dir: TaskDirFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(**_: object) -> SandboxBackend:
        msg = "cannot reach the Docker daemon"
        raise SandboxError(msg)

    monkeypatch.setattr(cli, "make_backend", unreachable)
    result = runner.invoke(cli.app, ["run", str(make_task_dir())])
    assert result.exit_code == cli.EXIT_ERROR
    assert "cannot reach the Docker daemon" in result.output


def test_validate_calibrated_task(
    make_task_dir: TaskDirFactory, fake_ports: FakeVerifier, tmp_path: Path
) -> None:
    fake_ports.verdicts = [passed(), passed(), failed(), failed()]
    out = tmp_path / "runs"
    result = runner.invoke(
        cli.app, ["validate", str(make_task_dir()), "--trials", "2", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "result: calibrated" in result.output
    report = json.loads(next(out.glob("demo-task/validate/*/calibration.json")).read_text())
    assert report["calibrated"] is True
    assert report["oracle"]["agent"] == "oracle"
    assert report["nop"]["agent"] == "nop"


def test_validate_uncalibrated_task_exits_nonzero(
    make_task_dir: TaskDirFactory, fake_ports: FakeVerifier, tmp_path: Path
) -> None:
    fake_ports.verdicts = [passed(), passed()]  # nop passes too
    result = runner.invoke(
        cli.app, ["validate", str(make_task_dir()), "--trials", "1", "--out", str(tmp_path)]
    )
    assert result.exit_code == cli.EXIT_ERROR
    assert "NOT CALIBRATED" in result.output
    assert "nop trial 0 scored 1" in result.output


def test_summary_keeps_timeout_verdict_reason(
    make_task_dir: TaskDirFactory, fake_ports: FakeVerifier, tmp_path: Path
) -> None:
    fake_ports.verdicts = [
        Verdict(
            passed=False, exit_code=None, output="verifier timed out after 300s", duration_seconds=1
        )
    ]
    result = runner.invoke(cli.app, ["run", str(make_task_dir()), "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert "verifier timed out after 300s" in result.output


def test_llm_agent_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CRUCIBLE_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY is not set"):
        cli.make_agent(cli.AgentName.LLM, Settings())


def test_llm_agent_is_built_from_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CRUCIBLE_LLM_MODEL", "claude-sonnet-5-5")
    agent = cli.make_agent(cli.AgentName.LLM, Settings())
    assert isinstance(agent, LlmAgent)
    assert agent.name == "llm"


def test_run_with_llm_agent_and_no_key_is_a_usage_level_error(
    make_task_dir: TaskDirFactory,
    fake_ports: FakeVerifier,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_dir = make_task_dir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(
        cli.app, ["run", str(task_dir), "--agent", "llm", "--out", str(tmp_path)]
    )
    assert result.exit_code == cli.EXIT_ERROR
    assert "ANTHROPIC_API_KEY is not set" in result.output


def test_make_backend_and_verifier_build_the_docker_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class StubBackend:
        def __init__(self, *, reuse_images: bool) -> None:
            created["reuse_images"] = reuse_images

    monkeypatch.setattr(cli, "DockerSandboxBackend", StubBackend)
    backend = cli.make_backend(reuse_images=False)
    assert created == {"reuse_images": False}
    assert isinstance(cli.make_verifier(backend), PytestVerifier)


def test_validate_reports_crucible_errors(
    make_task_dir: TaskDirFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli, "make_backend", lambda **_: FakeBackend(build_error=BuildError("no base image"))
    )
    monkeypatch.setattr(cli, "make_verifier", lambda _b: FakeVerifier())
    result = runner.invoke(cli.app, ["validate", str(make_task_dir()), "--out", str(tmp_path)])
    assert result.exit_code == cli.EXIT_ERROR
    assert "error: no base image" in result.output
