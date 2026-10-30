"""Docker adapter tests. Need a daemon; auto-skipped otherwise."""

from __future__ import annotations

import tarfile
import time
from collections.abc import Iterator
from pathlib import Path

import docker
import pytest

from crucible.adapters.docker_sandbox import MANAGED_LABEL, DockerSandboxBackend
from crucible.domain.schema import ResourceLimits
from crucible.errors import BuildError, SandboxError, SandboxFileNotFoundError, SandboxTimeoutError
from crucible.ports.sandbox import ImageRef

pytestmark = pytest.mark.docker

WORKDIR = "/home/agent/work"
LIMITS = ResourceLimits(cpus=0.5, memory_bytes=256 * 1024**2, pids_limit=64)

SANDBOX_DOCKERFILE = f"""\
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 agent
RUN mkdir -p {WORKDIR} && echo original > {WORKDIR}/original.txt && chown -R agent:agent {WORKDIR}
WORKDIR {WORKDIR}
USER agent
"""

ROOT_DOCKERFILE = """\
FROM python:3.12-slim
"""


@pytest.fixture(scope="module")
def backend() -> DockerSandboxBackend:
    return DockerSandboxBackend()


def _build(backend: DockerSandboxBackend, tmp: Path, dockerfile: str, tag: str) -> ImageRef:
    context = tmp / tag.replace("/", "_").replace(":", "_")
    context.mkdir()
    (context / "Dockerfile").write_text(dockerfile)
    return backend.build_image(context, tag=tag, timeout=600)


@pytest.fixture(scope="module")
def image(
    backend: DockerSandboxBackend, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ImageRef]:
    ref = _build(
        backend, tmp_path_factory.mktemp("ctx"), SANDBOX_DOCKERFILE, "crucible/test-sandbox:dev"
    )
    yield ref
    backend.remove_image(ref)


@pytest.fixture(scope="module")
def root_image(
    backend: DockerSandboxBackend, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ImageRef]:
    ref = _build(backend, tmp_path_factory.mktemp("ctx"), ROOT_DOCKERFILE, "crucible/test-root:dev")
    yield ref
    backend.remove_image(ref)


def test_exec_captures_streams_exit_code_and_workdir(
    backend: DockerSandboxBackend, image: ImageRef
) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        result = sb.exec("pwd && echo out && echo err >&2 && exit 3", timeout=30)
    assert result.exit_code == 3
    assert result.stdout == f"{WORKDIR}\nout\n"
    assert result.stderr == "err\n"
    assert not result.ok


def test_exec_honours_explicit_workdir(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        assert sb.exec("pwd", timeout=30, workdir="/tmp").stdout.strip() == "/tmp"


def test_runs_as_non_root_without_network(backend: DockerSandboxBackend, image: ImageRef) -> None:
    probe = "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\""
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        assert sb.exec("id -u", timeout=30).stdout.strip() == "1000"
        assert sb.exec(probe, timeout=30).exit_code != 0
        assert sb.exec("cat /proc/net/route | tail -n +2 | wc -l", timeout=30).stdout.strip() == "0"


def test_resource_limits_are_applied(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        memory = sb.exec("cat /sys/fs/cgroup/memory.max", timeout=30).stdout.strip()
        pids = sb.exec("cat /sys/fs/cgroup/pids.max", timeout=30).stdout.strip()
        cpu = sb.exec("cat /sys/fs/cgroup/cpu.max", timeout=30).stdout.strip()
    assert memory == str(LIMITS.memory_bytes)
    assert pids == str(LIMITS.pids_limit)
    quota, period = cpu.split()
    assert int(quota) / int(period) == pytest.approx(LIMITS.cpus)


def test_capabilities_are_dropped(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        line = sb.exec("grep CapBnd /proc/self/status", timeout=30).stdout
    assert line.split()[1] == "0000000000000000"


def test_write_read_roundtrip_in_nested_directory(
    backend: DockerSandboxBackend, image: ImageRef
) -> None:
    path = f"{WORKDIR}/pkg/sub/mod.py"
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        sb.write_file(path, b"x = 1\n", mode=0o640)
        assert sb.read_file(path) == b"x = 1\n"
        stat = sb.exec(f"stat -c '%u %a' {path}", timeout=30).stdout.strip()
        # The agent user can still edit the file afterwards.
        assert (
            sb.exec(f"echo 'y = 2' >> {path} && cat {path}", timeout=30).stdout == "x = 1\ny = 2\n"
        )
    assert stat == "1000 640"


def test_read_missing_file_raises_not_found(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        with pytest.raises(SandboxFileNotFoundError):
            sb.read_file(f"{WORKDIR}/missing.txt")
        with pytest.raises(SandboxError, match="not a regular file"):
            sb.read_file(WORKDIR)


def test_write_rejects_relative_path(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with (
        backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb,
        pytest.raises(SandboxError, match="absolute"),
    ):
        sb.write_file("relative.txt", b"")


def test_copy_in_directory_preserves_tree_and_mode(
    backend: DockerSandboxBackend, image: ImageRef, tmp_path: Path
) -> None:
    src = tmp_path / "solution"
    (src / "nested").mkdir(parents=True)
    (src / "solve.sh").write_text("#!/bin/sh\necho solved\n")
    (src / "solve.sh").chmod(0o755)
    (src / "nested" / "data.txt").write_text("42")
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        sb.copy_in(src, "/solution")
        assert sb.exec("/solution/solve.sh", timeout=30).stdout == "solved\n"
        assert sb.read_file("/solution/nested/data.txt") == b"42"
        assert sb.exec("stat -c %u /solution/solve.sh", timeout=30).stdout.strip() == "1000"


def test_copy_in_missing_source_raises(
    backend: DockerSandboxBackend, image: ImageRef, tmp_path: Path
) -> None:
    with (
        backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb,
        pytest.raises(SandboxError, match="does not exist"),
    ):
        sb.copy_in(tmp_path / "nope", "/solution")


def test_snapshot_exports_workdir(
    backend: DockerSandboxBackend, image: ImageRef, tmp_path: Path
) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        sb.write_file(f"{WORKDIR}/hello.txt", b"hi")
        snap = sb.snapshot(tmp_path / "ws.tar")
    assert snap.root == WORKDIR
    with tarfile.open(snap.archive) as tar:
        names = set(tar.getnames())
    assert "work/hello.txt" in names
    assert "work/original.txt" in names


def test_timeout_kills_sandbox(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        started = time.monotonic()
        with pytest.raises(SandboxTimeoutError, match=r"exceeded 1\.0s"):
            sb.exec("sleep 30", timeout=1)
        assert time.monotonic() - started < 10
        with pytest.raises(SandboxError, match="killed"):
            sb.exec("true", timeout=5)


def test_zero_timeout_kills_sandbox(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with (
        backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb,
        pytest.raises(SandboxTimeoutError, match="no time left"),
    ):
        sb.exec("true", timeout=0)


def test_output_is_capped(backend: DockerSandboxBackend, image: ImageRef) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        result = sb.exec("head -c 3000000 /dev/zero | tr '\\0' 'a'", timeout=60)
    assert result.ok
    assert len(result.stdout) < 1_100_000
    assert result.stdout.endswith("[... output truncated by crucible ...]\n")


def test_container_is_removed_after_context(backend: DockerSandboxBackend, image: ImageRef) -> None:
    client = docker.from_env()
    before = {c.id for c in client.containers.list(all=True, filters={"label": MANAGED_LABEL})}
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        sb.exec("true", timeout=30)
        during = {c.id for c in client.containers.list(all=True, filters={"label": MANAGED_LABEL})}
    after = {c.id for c in client.containers.list(all=True, filters={"label": MANAGED_LABEL})}
    assert len(during - before) == 1
    assert after == before


def test_container_is_removed_when_body_raises(
    backend: DockerSandboxBackend, image: ImageRef
) -> None:
    client = docker.from_env()
    before = {c.id for c in client.containers.list(all=True, filters={"label": MANAGED_LABEL})}
    with pytest.raises(RuntimeError), backend.start(image, workdir=WORKDIR, limits=LIMITS):
        raise RuntimeError("boom")
    after = {c.id for c in client.containers.list(all=True, filters={"label": MANAGED_LABEL})}
    assert after == before


def test_build_reports_image_user(image: ImageRef, root_image: ImageRef) -> None:
    assert image.user == "agent"
    assert root_image.user == ""


def test_root_image_is_refused_unless_allowed(
    backend: DockerSandboxBackend, root_image: ImageRef
) -> None:
    with (
        pytest.raises(SandboxError, match="runs as root"),
        backend.start(root_image, workdir="/tmp", limits=LIMITS),
    ):
        pass
    permissive = DockerSandboxBackend(allow_root=True)
    with permissive.start(root_image, workdir="/tmp", limits=LIMITS) as sb:
        assert sb.exec("id -u", timeout=30).stdout.strip() == "0"


def test_build_failure_reports_log_tail(backend: DockerSandboxBackend, tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\nRUN exit 7\n")
    with pytest.raises(BuildError, match="failed with exit code 1"):
        backend.build_image(tmp_path, tag="crucible/test-broken:dev", timeout=600)


def test_build_timeout(backend: DockerSandboxBackend, tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\nRUN sleep 60\n")
    with pytest.raises(BuildError, match="exceeded 3s"):
        backend.build_image(tmp_path, tag="crucible/test-slow:dev", timeout=3)


def test_build_without_dockerfile(backend: DockerSandboxBackend, tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="no Dockerfile"):
        backend.build_image(tmp_path, tag="crucible/test-none:dev", timeout=10)


def test_remove_missing_image_is_quiet(backend: DockerSandboxBackend) -> None:
    backend.remove_image(ImageRef(tag="crucible/does-not-exist:never", id="sha256:0", user="x"))


def test_unreachable_daemon_is_a_sandbox_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    with pytest.raises(SandboxError, match="cannot reach the Docker daemon"):
        DockerSandboxBackend()


def test_restore_recreates_snapshot_exactly(
    backend: DockerSandboxBackend, image: ImageRef, tmp_path: Path
) -> None:
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        sb.exec("rm original.txt && mkdir -p pkg && echo 'v = 1' > pkg/mod.py", timeout=30)
        snap = sb.snapshot(tmp_path / "ws.tar")
    with backend.start(image, workdir=WORKDIR, limits=LIMITS) as sb:
        sb.exec("find . -mindepth 1 -delete", timeout=30)
        sb.restore(snap)
        listing = sb.exec(
            "find . -type f | sort && cat pkg/mod.py && stat -c %u pkg/mod.py", timeout=30
        ).stdout
    assert listing == "./pkg/mod.py\nv = 1\n1000\n"
