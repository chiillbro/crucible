"""CLI tests. Docker is replaced by fakes through the factory hooks in ``crucible.cli``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from crucible import __version__, cli
from crucible.domain.schema import Verdict
from crucible.errors import BuildError, SandboxError
from crucible.ports import SandboxBackend
from tests.conftest import TaskDirFactory
from tests.fakes import FakeBackend, FakeVerifier, failed, passed

runner = CliRunner()


@pytest.fixture
def fake_ports(monkeypatch: pytest.MonkeyPatch) -> FakeVerifier:
    verifier = FakeVerifier()
    monkeypatch.setattr(cli, "make_backend", FakeBackend)
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
        cli, "make_backend", lambda: FakeBackend(build_error=BuildError("step failed"))
    )
    monkeypatch.setattr(cli, "make_verifier", lambda _b: FakeVerifier())
    result = runner.invoke(cli.app, ["run", str(make_task_dir())])
    assert result.exit_code == cli.EXIT_ERROR
    assert "error: step failed" in result.output


def test_run_reports_unreachable_backend(
    make_task_dir: TaskDirFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable() -> SandboxBackend:
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
