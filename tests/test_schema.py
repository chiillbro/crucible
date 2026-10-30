"""Task schema and loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from crucible.domain.schema import (
    AgentSpec,
    Difficulty,
    TaskConfig,
    load_task,
    parse_memory,
)
from crucible.errors import TaskLoadError
from tests.conftest import MINIMAL_TASK_TOML, TaskDirFactory

FULL_TASK_TOML = """\
[task]
name = "fix-thing"
description = "Fix the thing."
difficulty = "medium"
tags = ["python"]

[environment]
workdir = "/app/"
build_timeout_seconds = 120

[agent]
timeout_seconds = 60
cpus = 2
memory = "512m"
pids_limit = 64

[verifier]
timeout_seconds = 30
"""


def test_load_minimal_task_applies_defaults(make_task_dir: TaskDirFactory) -> None:
    task = load_task(make_task_dir())
    assert task.name == "demo-task"
    assert task.config.task.difficulty is Difficulty.EASY
    assert task.workdir == "/workspace"
    assert task.config.agent.timeout_seconds == 900
    assert task.config.agent.memory_bytes == 1024**3
    assert task.config.verifier.timeout_seconds == 300
    assert task.instruction == "Fix the bug."
    assert task.environment_dir == task.root / "environment"
    assert task.solution_dir == task.root / "solution"
    assert task.tests_dir == task.root / "tests"


def test_load_full_task(make_task_dir: TaskDirFactory) -> None:
    task = load_task(make_task_dir(toml=FULL_TASK_TOML))
    assert task.workdir == "/app"  # trailing slash normalised
    assert task.config.environment.build_timeout_seconds == 120
    limits = task.limits
    assert (limits.cpus, limits.memory_bytes, limits.pids_limit) == (2.0, 512 * 1024**2, 64)


def test_load_resolves_relative_path(
    make_task_dir: TaskDirFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_task_dir()
    monkeypatch.chdir(root.parent)
    task = load_task(Path(root.name))
    assert task.root.is_absolute()


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(TaskLoadError, match="task directory not found"):
        load_task(tmp_path / "nope")


@pytest.mark.parametrize(
    "relative",
    [
        "task.toml",
        "instruction.md",
        "environment/Dockerfile",
        "solution/solve.sh",
        "tests/Dockerfile",
    ],
)
def test_missing_required_file(make_task_dir: TaskDirFactory, relative: str) -> None:
    root = make_task_dir()
    (root / relative).unlink()
    with pytest.raises(TaskLoadError, match=f"missing required files: {relative}"):
        load_task(root)


def test_invalid_toml_syntax(make_task_dir: TaskDirFactory) -> None:
    with pytest.raises(TaskLoadError, match="invalid TOML"):
        load_task(make_task_dir(toml="[task\nname = 'x'"))


def test_unknown_key_is_rejected(make_task_dir: TaskDirFactory) -> None:
    toml = MINIMAL_TASK_TOML + "\n[agent]\ntimeout = 5\n"
    with pytest.raises(TaskLoadError, match=r"agent\.timeout: Extra inputs are not permitted"):
        load_task(make_task_dir(toml=toml))


def test_missing_task_table(make_task_dir: TaskDirFactory) -> None:
    with pytest.raises(TaskLoadError, match="task: Field required"):
        load_task(make_task_dir(toml="[agent]\ntimeout_seconds = 5\n"))


@pytest.mark.parametrize("name", ["Fix Thing", "-leading", "has_underscore", "a" * 64])
def test_bad_task_name(make_task_dir: TaskDirFactory, name: str) -> None:
    toml = MINIMAL_TASK_TOML.replace('name = "demo-task"', f'name = "{name}"')
    with pytest.raises(TaskLoadError, match=r"task\.name"):
        load_task(make_task_dir(toml=toml))


def test_bad_difficulty(make_task_dir: TaskDirFactory) -> None:
    toml = MINIMAL_TASK_TOML.replace('"easy"', '"brutal"')
    with pytest.raises(TaskLoadError, match=r"task\.difficulty"):
        load_task(make_task_dir(toml=toml))


@pytest.mark.parametrize("workdir", ["relative/path", "/"])
def test_bad_workdir(make_task_dir: TaskDirFactory, workdir: str) -> None:
    toml = MINIMAL_TASK_TOML + f'\n[environment]\nworkdir = "{workdir}"\n'
    with pytest.raises(TaskLoadError, match="workdir must be an absolute path"):
        load_task(make_task_dir(toml=toml))


@pytest.mark.parametrize("timeout", ["0", "-5"])
def test_non_positive_timeout(make_task_dir: TaskDirFactory, timeout: str) -> None:
    toml = MINIMAL_TASK_TOML + f"\n[agent]\ntimeout_seconds = {timeout}\n"
    with pytest.raises(TaskLoadError, match=r"agent\.timeout_seconds"):
        load_task(make_task_dir(toml=toml))


def test_empty_instruction(make_task_dir: TaskDirFactory) -> None:
    with pytest.raises(TaskLoadError, match=r"instruction\.md is empty"):
        load_task(make_task_dir(instruction="  \n"))


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1g", 1024**3),
        ("512m", 512 * 1024**2),
        ("2048k", 2048 * 1024),
        ("1048576", 1048576),
        (" 2G ", 2 * 1024**3),
    ],
)
def test_parse_memory(spec: str, expected: int) -> None:
    assert parse_memory(spec) == expected


@pytest.mark.parametrize("spec", ["", "1gb", "1.5g", "lots", "-1g"])
def test_parse_memory_rejects_garbage(spec: str) -> None:
    with pytest.raises(ValueError, match="invalid memory limit"):
        parse_memory(spec)


def test_agent_spec_rejects_tiny_memory() -> None:
    with pytest.raises(ValueError, match="below the 6m minimum"):
        AgentSpec(memory="1m")


def test_task_config_is_immutable() -> None:
    config = TaskConfig.model_validate(
        {"task": {"name": "x", "description": "d", "difficulty": "hard"}}
    )
    with pytest.raises(Exception, match="frozen"):
        config.task.name = "y"  # type: ignore[misc]
