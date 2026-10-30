"""PytestVerifier behaviour with a fake backend."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from crucible.adapters.pytest_verifier import PytestVerifier
from crucible.domain.schema import Task, load_task
from crucible.errors import SandboxTimeoutError, VerifierError
from crucible.ports.sandbox import ExecResult, ImageRef, WorkspaceSnapshot
from tests.conftest import TaskDirFactory
from tests.fakes import FakeBackend, FakeSandbox

ENV_IMAGE = ImageRef(tag="crucible/demo-task-env:abc", id="sha256:env", user="agent")


@pytest.fixture
def task(make_task_dir: TaskDirFactory) -> Task:
    return load_task(make_task_dir())


def _snapshot(tmp_path: Path, root: str = "/workspace") -> WorkspaceSnapshot:
    archive = tmp_path / "ws.tar"
    archive.write_bytes(b"tar")
    return WorkspaceSnapshot(archive=archive, root=root)


def _exec(exit_code: int, stdout: str = "", stderr: str = "") -> ExecResult:
    return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=1.0)


def test_prepare_builds_clean_base_then_tests_image(task: Task) -> None:
    backend = FakeBackend()
    verifier = PytestVerifier(backend)
    images = verifier.prepare(task, ENV_IMAGE)
    assert len(backend.builds) == 2

    base_context, base_tag, base_args, base_timeout = backend.builds[0]
    assert base_tag.startswith("crucible/demo-task-verifier-base:")
    assert base_args == {"BASE_IMAGE": ENV_IMAGE.tag, "BASE_USER": "agent", "WORKDIR": "/workspace"}
    assert base_timeout == task.config.environment.build_timeout_seconds
    assert not base_context.exists()  # generated context is temporary

    context, tag, build_args, _ = backend.builds[1]
    assert context == task.tests_dir
    assert tag.startswith("crucible/demo-task-verifier:")
    assert build_args == {"BASE_IMAGE": base_tag}
    assert [image.tag for image in images] == [base_tag, tag]


def test_generated_base_dockerfile_deletes_workdir_and_restores_user(task: Task) -> None:
    captured: dict[str, str] = {}

    class CapturingBackend(FakeBackend):
        def build_image(self, context: Path, **kwargs: object) -> ImageRef:
            if "verifier-base" in str(kwargs["tag"]):
                captured["dockerfile"] = (context / "Dockerfile").read_text()
            return super().build_image(context, **kwargs)  # type: ignore[arg-type]

    PytestVerifier(CapturingBackend()).prepare(task, ENV_IMAGE)
    dockerfile = captured["dockerfile"]
    assert "FROM ${BASE_IMAGE}" in dockerfile
    assert 'RUN rm -rf -- "${WORKDIR}"' in dockerfile
    assert dockerfile.rstrip().endswith("USER ${BASE_USER}")


def test_verify_before_prepare_is_an_error(task: Task, tmp_path: Path) -> None:
    with pytest.raises(VerifierError, match="before prepare"):
        PytestVerifier(FakeBackend()).verify(task, _snapshot(tmp_path))


def test_verify_checks_clean_workdir_restores_snapshot_and_pins_pytest(
    task: Task, tmp_path: Path
) -> None:
    backend = FakeBackend(
        sandbox_factory=lambda: FakeSandbox(handler=lambda _c: _exec(0, "1 passed"))
    )
    verifier = PytestVerifier(backend)
    verifier.prepare(task, ENV_IMAGE)
    snapshot = _snapshot(tmp_path)
    verdict = verifier.verify(task, snapshot)

    image, workdir, limits = backend.starts[0]
    assert image.tag.startswith("crucible/demo-task-verifier:")
    assert workdir == "/tests"
    assert limits == task.limits
    sandbox = backend.sandboxes[0]
    probe, _, _ = sandbox.commands[0]
    assert probe == """[ ! -e '/workspace' ] || [ -z "$(ls -A '/workspace')" ]"""
    assert sandbox.restored == [snapshot]
    command, timeout, _ = sandbox.commands[1]
    assert command.startswith("python -m pytest")
    assert "--rootdir=/tests" in command
    assert "--confcutdir=/tests" in command
    assert "-p no:cacheprovider" in command
    assert command.endswith(" /tests")
    assert timeout == task.config.verifier.timeout_seconds
    assert sandbox.closed
    assert verdict.passed
    assert verdict.reward == 1
    assert verdict.exit_code == 0
    assert verdict.output == "1 passed"


def _pytest_handler(
    exit_code: int, stdout: str = "", stderr: str = ""
) -> Callable[[str], ExecResult]:
    """Answer the clean-workdir probe with success and pytest with ``exit_code``."""

    def handle(command: str) -> ExecResult:
        if command.startswith("python -m pytest"):
            return _exec(exit_code, stdout, stderr)
        return _exec(0)

    return handle


def test_dirty_workdir_in_verifier_image_is_an_error(task: Task, tmp_path: Path) -> None:
    def handle(command: str) -> ExecResult:
        if command.startswith("[ ! -e"):
            return _exec(1)
        raise AssertionError("pytest must not run on a dirty verifier image")

    backend = FakeBackend(sandbox_factory=lambda: FakeSandbox(handler=handle))
    verifier = PytestVerifier(backend)
    verifier.prepare(task, ENV_IMAGE)
    with pytest.raises(VerifierError, match="contains files under /workspace"):
        verifier.verify(task, _snapshot(tmp_path))
    assert backend.sandboxes[0].restored == []


@pytest.mark.parametrize(("exit_code", "passed"), [(0, True), (1, False), (2, False)])
def test_exit_code_mapping(task: Task, tmp_path: Path, exit_code: int, passed: bool) -> None:
    backend = FakeBackend(sandbox_factory=lambda: FakeSandbox(handler=_pytest_handler(exit_code)))
    verifier = PytestVerifier(backend)
    verifier.prepare(task, ENV_IMAGE)
    verdict = verifier.verify(task, _snapshot(tmp_path))
    assert verdict.passed is passed
    assert verdict.reward == (1 if passed else 0)


@pytest.mark.parametrize(
    ("exit_code", "match"),
    [(5, "no tests collected"), (3, "unexpected code 3"), (4, "unexpected code 4")],
)
def test_infrastructure_exit_codes_raise(
    task: Task, tmp_path: Path, exit_code: int, match: str
) -> None:
    backend = FakeBackend(
        sandbox_factory=lambda: FakeSandbox(handler=_pytest_handler(exit_code, stderr="usage"))
    )
    verifier = PytestVerifier(backend)
    verifier.prepare(task, ENV_IMAGE)
    with pytest.raises(VerifierError, match=match):
        verifier.verify(task, _snapshot(tmp_path))


def test_timeout_is_a_failed_verdict(task: Task, tmp_path: Path) -> None:
    def hang(command: str) -> ExecResult:
        if command.startswith("python -m pytest"):
            raise SandboxTimeoutError("too slow")
        return _exec(0)

    backend = FakeBackend(sandbox_factory=lambda: FakeSandbox(handler=hang))
    verifier = PytestVerifier(backend)
    verifier.prepare(task, ENV_IMAGE)
    verdict = verifier.verify(task, _snapshot(tmp_path))
    assert not verdict.passed
    assert verdict.exit_code is None
    assert "timed out after 300s" in verdict.output
    assert backend.sandboxes[0].closed


def test_snapshot_root_must_match_workdir(task: Task, tmp_path: Path) -> None:
    verifier = PytestVerifier(FakeBackend())
    verifier.prepare(task, ENV_IMAGE)
    with pytest.raises(VerifierError, match="expected /workspace"):
        verifier.verify(task, _snapshot(tmp_path, root="/elsewhere"))


def test_output_is_tail_truncated(task: Task, tmp_path: Path) -> None:
    backend = FakeBackend(
        sandbox_factory=lambda: FakeSandbox(handler=_pytest_handler(1, "x" * 100 + "END"))
    )
    verifier = PytestVerifier(backend, output_limit=10)
    verifier.prepare(task, ENV_IMAGE)
    verdict = verifier.verify(task, _snapshot(tmp_path))
    assert verdict.output == "[... truncated ...]\nxxxxxxxEND"
