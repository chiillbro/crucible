"""Shared fixtures."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

MINIMAL_TASK_TOML = """\
[task]
name = "demo-task"
description = "A demo task."
difficulty = "easy"
tags = ["demo"]
"""

TaskDirFactory = Callable[..., Path]


@pytest.fixture
def make_task_dir(tmp_path: Path) -> TaskDirFactory:
    """Return a factory that writes a structurally valid task directory."""

    def _make(
        name: str = "task",
        *,
        toml: str = MINIMAL_TASK_TOML,
        instruction: str = "Fix the bug.",
    ) -> Path:
        root = tmp_path / name
        (root / "environment").mkdir(parents=True)
        (root / "solution").mkdir()
        (root / "tests").mkdir()
        (root / "task.toml").write_text(toml)
        (root / "instruction.md").write_text(instruction)
        (root / "environment" / "Dockerfile").write_text("FROM scratch\n")
        (root / "solution" / "solve.sh").write_text("#!/bin/sh\n")
        (root / "tests" / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")
        (root / "tests" / "test_hidden.py").write_text("def test_x():\n    assert True\n")
        return root

    return _make


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:  # any failure means "no usable daemon"
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``@pytest.mark.docker`` tests when no daemon is reachable.

    Set ``CRUCIBLE_REQUIRE_DOCKER=1`` (CI does) to fail instead of skipping, so a broken
    Docker setup can never masquerade as a green run.
    """
    docker_items = [item for item in items if "docker" in item.keywords]
    if not docker_items or _docker_available():
        return
    if os.environ.get("CRUCIBLE_REQUIRE_DOCKER"):
        pytest.exit("CRUCIBLE_REQUIRE_DOCKER is set but the Docker daemon is unreachable", 1)
    skip = pytest.mark.skip(reason="Docker daemon not available")
    for item in docker_items:
        item.add_marker(skip)
