"""In-memory fakes for the ports. Used by runner, agent and verifier unit tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from crucible.deadline import Deadline
from crucible.domain.schema import AgentOutcome, ResourceLimits, Task, Verdict
from crucible.errors import SandboxError, SandboxFileNotFoundError
from crucible.ports.sandbox import ExecResult, ImageRef, Sandbox, WorkspaceSnapshot

ExecHandler = Callable[[str], ExecResult]


def ok(stdout: str = "", stderr: str = "") -> ExecResult:
    return ExecResult(exit_code=0, stdout=stdout, stderr=stderr, duration_seconds=0.01)


def fail(exit_code: int = 1, stdout: str = "", stderr: str = "boom") -> ExecResult:
    return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=0.01)


@dataclass
class FakeSandbox:
    workdir: str = "/workspace"
    handler: ExecHandler = field(default=lambda _cmd: ok())
    files: dict[str, bytes] = field(default_factory=dict)
    commands: list[tuple[str, float, str | None]] = field(default_factory=list)
    copied: list[tuple[Path, str]] = field(default_factory=list)
    restored: list[WorkspaceSnapshot] = field(default_factory=list)
    killed: bool = False
    closed: bool = False

    def exec(self, command: str, *, timeout: float, workdir: str | None = None) -> ExecResult:
        self._ensure_alive()
        self.commands.append((command, timeout, workdir))
        return self.handler(command)

    def read_file(self, path: str) -> bytes:
        self._ensure_alive()
        try:
            return self.files[path]
        except KeyError as exc:
            msg = f"no such file in sandbox: {path}"
            raise SandboxFileNotFoundError(msg) from exc

    def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        self._ensure_alive()
        self.files[path] = content

    def copy_in(self, source: Path, dest: str) -> None:
        self._ensure_alive()
        self.copied.append((source, dest))

    def snapshot(self, dest: Path) -> WorkspaceSnapshot:
        dest.write_bytes(b"fake-tar:" + repr(sorted(self.files)).encode())
        return WorkspaceSnapshot(archive=dest, root=self.workdir)

    def restore(self, snapshot: WorkspaceSnapshot) -> None:
        self._ensure_alive()
        self.restored.append(snapshot)

    def kill(self) -> None:
        self.killed = True

    def _ensure_alive(self) -> None:
        if self.killed:
            msg = "sandbox has been killed"
            raise SandboxError(msg)


@dataclass
class FakeBackend:
    sandbox_factory: Callable[[], FakeSandbox] = FakeSandbox
    build_error: Exception | None = None
    start_error: Exception | None = None
    builds: list[tuple[Path, str, dict[str, str], float]] = field(default_factory=list)
    starts: list[tuple[ImageRef, str, ResourceLimits]] = field(default_factory=list)
    sandboxes: list[FakeSandbox] = field(default_factory=list)
    removed: list[ImageRef] = field(default_factory=list)

    def build_image(
        self,
        context: Path,
        *,
        tag: str,
        build_args: Mapping[str, str] | None = None,
        timeout: float,
    ) -> ImageRef:
        self.builds.append((context, tag, dict(build_args or {}), timeout))
        if self.build_error is not None:
            raise self.build_error
        return ImageRef(tag=tag, id=f"sha256:{len(self.builds):064x}", user="agent")

    @contextmanager
    def start(
        self,
        image: ImageRef,
        *,
        workdir: str,
        limits: ResourceLimits,
    ) -> Iterator[FakeSandbox]:
        self.starts.append((image, workdir, limits))
        if self.start_error is not None:
            raise self.start_error
        sandbox = self.sandbox_factory()
        sandbox.workdir = workdir
        self.sandboxes.append(sandbox)
        try:
            yield sandbox
        finally:
            sandbox.closed = True

    def remove_image(self, image: ImageRef) -> None:
        self.removed.append(image)


SolveFn = Callable[[Sandbox, Task, Deadline], AgentOutcome]


@dataclass
class ScriptedAgent:
    solve_fn: SolveFn = field(default=lambda _sb, _task, _deadline: AgentOutcome(steps=1))
    agent_name: str = "scripted"
    calls: int = 0

    @property
    def name(self) -> str:
        return self.agent_name

    def solve(self, sandbox: Sandbox, task: Task, deadline: Deadline) -> AgentOutcome:
        self.calls += 1
        return self.solve_fn(sandbox, task, deadline)


@dataclass
class FakeVerifier:
    verdicts: list[Verdict | Exception] = field(default_factory=list)
    prepared: list[tuple[Task, ImageRef]] = field(default_factory=list)
    verified: list[WorkspaceSnapshot] = field(default_factory=list)
    images: tuple[ImageRef, ...] = (
        ImageRef(tag="crucible/fake-verifier:1", id="sha256:v", user="agent"),
    )

    def prepare(self, task: Task, environment: ImageRef) -> tuple[ImageRef, ...]:
        self.prepared.append((task, environment))
        return self.images

    def verify(self, task: Task, snapshot: WorkspaceSnapshot) -> Verdict:
        self.verified.append(snapshot)
        outcome = self.verdicts.pop(0) if self.verdicts else passed()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def passed(output: str = "1 passed") -> Verdict:
    return Verdict(passed=True, exit_code=0, output=output, duration_seconds=0.5)


def failed(output: str = "1 failed") -> Verdict:
    return Verdict(passed=False, exit_code=1, output=output, duration_seconds=0.5)
