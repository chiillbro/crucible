"""Typed task schema, task loading, and result models."""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    field_validator,
)

from crucible.errors import TaskLoadError

# Docker refuses memory limits under 6 MiB; fail early with a clearer message.
MIN_MEMORY_BYTES = 6 * 1024 * 1024
_MEMORY_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[kmg]?)$")
_UNIT_MULTIPLIER = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def parse_memory(spec: str) -> int:
    """Parse a Docker-style memory string (``512m``, ``2g``, ``1048576``) into bytes."""
    match = _MEMORY_RE.match(spec.strip().lower())
    if match is None:
        msg = f"invalid memory limit {spec!r}; expected an integer with optional k/m/g suffix"
        raise ValueError(msg)
    return int(match["value"]) * _UNIT_MULTIPLIER[match["unit"]]


class _StrictModel(BaseModel):
    """Base for task.toml tables: unknown keys are errors, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TaskMeta(_StrictModel):
    """The ``[task]`` table."""

    name: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}$",
        description="Slug used for image tags and result files.",
    )
    description: str = Field(min_length=1)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list)


class EnvironmentSpec(_StrictModel):
    """The ``[environment]`` table: how the task image is built and where the code lives."""

    workdir: str = Field(
        default="/workspace", description="Absolute path of the agent's workspace."
    )
    build_timeout_seconds: PositiveFloat = 600

    @field_validator("workdir")
    @classmethod
    def _absolute_workdir(cls, value: str) -> str:
        if not value.startswith("/") or value == "/":
            msg = "workdir must be an absolute path other than '/'"
            raise ValueError(msg)
        return value.rstrip("/")


class AgentSpec(_StrictModel):
    """The ``[agent]`` table: the solve-phase budget and container limits."""

    timeout_seconds: PositiveFloat = 900
    cpus: PositiveFloat = 1.0
    memory: str = "1g"
    pids_limit: PositiveInt = 256

    @field_validator("memory")
    @classmethod
    def _valid_memory(cls, value: str) -> str:
        if parse_memory(value) < MIN_MEMORY_BYTES:
            msg = f"memory limit {value!r} is below the 6m minimum"
            raise ValueError(msg)
        return value

    @property
    def memory_bytes(self) -> int:
        return parse_memory(self.memory)


class VerifierSpec(_StrictModel):
    """The ``[verifier]`` table."""

    timeout_seconds: PositiveFloat = 300


class TaskConfig(_StrictModel):
    """The whole ``task.toml``."""

    task: TaskMeta
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    agent: AgentSpec = Field(default_factory=AgentSpec)
    verifier: VerifierSpec = Field(default_factory=VerifierSpec)


class ResourceLimits(BaseModel):
    """Backend-neutral container limits."""

    model_config = ConfigDict(frozen=True)

    cpus: float
    memory_bytes: int
    pids_limit: int


class Task(BaseModel):
    """A task loaded from disk with its layout resolved.

    Layout (relative to ``root``)::

        task.toml                 metadata and limits
        instruction.md            what the agent is told
        environment/Dockerfile    the broken codebase, built into the environment image
        solution/solve.sh         reference solution applied by the oracle agent
        tests/                    hidden tests plus the verifier Dockerfile
    """

    model_config = ConfigDict(frozen=True)

    root: Path
    config: TaskConfig
    instruction: str

    @property
    def name(self) -> str:
        return self.config.task.name

    @property
    def workdir(self) -> str:
        return self.config.environment.workdir

    @property
    def environment_dir(self) -> Path:
        return self.root / "environment"

    @property
    def solution_dir(self) -> Path:
        return self.root / "solution"

    @property
    def tests_dir(self) -> Path:
        return self.root / "tests"

    @property
    def limits(self) -> ResourceLimits:
        spec = self.config.agent
        return ResourceLimits(
            cpus=spec.cpus, memory_bytes=spec.memory_bytes, pids_limit=spec.pids_limit
        )


REQUIRED_FILES = (
    "task.toml",
    "instruction.md",
    "environment/Dockerfile",
    "solution/solve.sh",
    "tests/Dockerfile",
)


def load_task(task_dir: Path) -> Task:
    """Load and validate a task directory.

    Raises:
        TaskLoadError: if the directory, a required file, or the schema is invalid.
    """
    root = task_dir.resolve()
    if not root.is_dir():
        msg = f"task directory not found: {root}"
        raise TaskLoadError(msg)
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        msg = f"task {root} is missing required files: {', '.join(missing)}"
        raise TaskLoadError(msg)

    try:
        raw = tomllib.loads((root / "task.toml").read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        msg = f"{root / 'task.toml'}: invalid TOML: {exc}"
        raise TaskLoadError(msg) from exc
    try:
        config = TaskConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        msg = f"{root / 'task.toml'}: {details}"
        raise TaskLoadError(msg) from exc

    instruction = (root / "instruction.md").read_text(encoding="utf-8").strip()
    if not instruction:
        msg = f"{root / 'instruction.md'} is empty"
        raise TaskLoadError(msg)

    return Task(root=root, config=config, instruction=instruction)


# --- Results -------------------------------------------------------------------------

Reward = Literal[0, 1]


class TrialStatus(StrEnum):
    """Terminal state of one trial. Only ``PASSED`` earns reward 1."""

    PASSED = "passed"
    FAILED = "failed"
    AGENT_ERROR = "agent_error"
    AGENT_TIMEOUT = "agent_timeout"
    SANDBOX_ERROR = "sandbox_error"
    VERIFIER_ERROR = "verifier_error"


class AgentOutcome(BaseModel):
    """What an agent reports about its own run. Informational; never affects the reward."""

    model_config = ConfigDict(frozen=True)

    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "done"
    summary: str | None = None


class Verdict(BaseModel):
    """The verifier's decision for one trial."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    exit_code: int | None
    output: str
    duration_seconds: float

    @property
    def reward(self) -> Reward:
        return 1 if self.passed else 0


class TrialResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    status: TrialStatus
    reward: Reward
    reason: str | None = None
    duration_seconds: float
    agent: AgentOutcome | None = None
    verdict: Verdict | None = None


class Score(BaseModel):
    """pass@k and summary statistics over a set of trial rewards.

    ``pass_at_k`` is ``None`` when it is undefined: no trials, or ``k`` larger than ``n``.
    """

    model_config = ConfigDict(frozen=True)

    n: int = Field(ge=0)
    c: int = Field(ge=0)
    k: int = Field(ge=1)
    pass_at_k: float | None
    pass_at_1: float | None
    mean_reward: float | None


class TaskResult(BaseModel):
    """The structured output of one ``crucible run``; serialised to ``results.json``."""

    model_config = ConfigDict(frozen=True)

    crucible_version: str
    task_name: str
    task_dir: str
    agent: str
    started_at: datetime
    finished_at: datetime
    images: dict[str, str]
    trials: list[TrialResult]
    score: Score
