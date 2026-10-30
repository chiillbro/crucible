"""Shared fixtures."""

from __future__ import annotations

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
