"""Command-line interface: ``crucible run`` and ``crucible validate``."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from crucible import __version__
from crucible.adapters.agents import NopAgent, OracleAgent
from crucible.adapters.docker_sandbox import DockerSandboxBackend
from crucible.adapters.pytest_verifier import PytestVerifier
from crucible.config import Settings
from crucible.domain.calibration import Calibration
from crucible.domain.schema import Task, TaskResult, load_task
from crucible.errors import CrucibleError
from crucible.logging import configure_logging
from crucible.ports import Agent, SandboxBackend, Verifier
from crucible.report import format_calibration, format_summary
from crucible.runner import Runner, dump_result

log = logging.getLogger(__name__)

app = typer.Typer(
    help="Sandboxed evaluation harness for coding agents.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


class AgentName(StrEnum):
    ORACLE = "oracle"
    NOP = "nop"


def make_backend() -> SandboxBackend:
    return DockerSandboxBackend()


def make_verifier(backend: SandboxBackend) -> Verifier:
    return PytestVerifier(backend)


def make_agent(name: AgentName, settings: Settings) -> Agent:  # noqa: ARG001
    if name is AgentName.ORACLE:
        return OracleAgent()
    return NopAgent()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"crucible {__version__}")
        raise typer.Exit


@app.callback()
def main(
    log_level: Annotated[
        str | None, typer.Option("--log-level", help="DEBUG, INFO, WARNING or ERROR.")
    ] = None,
    _version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    settings = Settings()
    configure_logging(log_level or settings.log_level)


def _load(task_dir: Path) -> Task:
    try:
        return load_task(task_dir)
    except CrucibleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from exc


def _run_dir(out: Path, task: Task, label: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return out / task.name / label / stamp


def _execute(
    task: Task, agent: Agent, *, trials: int, k: int, run_dir: Path, rm_images: bool
) -> TaskResult:
    backend = make_backend()
    runner = Runner(
        backend=backend,
        agent=agent,
        verifier=make_verifier(backend),
        artifacts_dir=run_dir,
        remove_images=rm_images,
    )
    return runner.run(task, trials=trials, k=k)


TaskDirArg = Annotated[Path, typer.Argument(help="Task directory containing task.toml.")]
TrialsOpt = Annotated[int, typer.Option("--trials", "-n", min=1, help="Trials to run.")]
OutOpt = Annotated[Path, typer.Option("--out", help="Directory for run artifacts.")]
RmImagesOpt = Annotated[
    bool, typer.Option("--rm-images", help="Delete the images this run built when done.")
]


@app.command()
def run(
    task_dir: TaskDirArg,
    agent: Annotated[
        AgentName, typer.Option("--agent", "-a", help="Agent to evaluate.")
    ] = AgentName.ORACLE,
    trials: TrialsOpt = 1,
    k: Annotated[int, typer.Option("--k", min=1, help="k for pass@k.")] = 1,
    out: OutOpt = Path("runs"),
    rm_images: RmImagesOpt = False,
) -> None:
    """Evaluate an agent on a task over N trials and report pass@k."""
    task = _load(task_dir)
    run_dir = _run_dir(out, task, agent.value)
    try:
        result = _execute(
            task,
            make_agent(agent, Settings()),
            trials=trials,
            k=k,
            run_dir=run_dir,
            rm_images=rm_images,
        )
    except CrucibleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    results_path = run_dir / "results.json"
    dump_result(result, results_path)
    typer.echo(format_summary(result))
    typer.echo(f"\nresults: {results_path}")


@app.command()
def validate(
    task_dir: TaskDirArg,
    trials: TrialsOpt = 3,
    out: OutOpt = Path("runs"),
    rm_images: RmImagesOpt = False,
) -> None:
    """Check calibration: the oracle must always pass and nop must always fail."""
    task = _load(task_dir)
    run_dir = _run_dir(out, task, "validate")
    try:
        oracle = _execute(
            task, OracleAgent(), trials=trials, k=1, run_dir=run_dir / "oracle", rm_images=False
        )
        nop = _execute(
            task, NopAgent(), trials=trials, k=1, run_dir=run_dir / "nop", rm_images=rm_images
        )
    except CrucibleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    calibration = Calibration(oracle=oracle, nop=nop)
    report_path = run_dir / "calibration.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "calibrated": calibration.ok,
                "problems": calibration.problems(),
                "oracle": oracle.model_dump(mode="json"),
                "nop": nop.model_dump(mode="json"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    typer.echo(format_calibration(calibration))
    typer.echo(f"\nreport: {report_path}")
    if not calibration.ok:
        raise typer.Exit(EXIT_ERROR)
