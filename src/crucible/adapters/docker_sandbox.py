"""Docker implementation of the sandbox port.

Images are built with BuildKit through the ``docker`` CLI; everything else (containers,
exec, archives) goes through the Docker SDK. See ADR 0002 for why the build step is
not done through the SDK.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tarfile
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import docker
import docker.errors

from crucible.domain.schema import ResourceLimits
from crucible.errors import (
    BuildError,
    SandboxError,
    SandboxFileNotFoundError,
    SandboxTimeoutError,
)
from crucible.ports.sandbox import ExecResult, ImageRef, WorkspaceSnapshot

log = logging.getLogger(__name__)

MANAGED_LABEL = "crucible.managed"
MAX_OUTPUT_BYTES = 1 << 20
_TRUNCATED_MARKER = "\n[... output truncated by crucible ...]\n"
_BUILD_LOG_TAIL_LINES = 40
_HOUSEKEEPING_TIMEOUT = 30.0


class DockerSandboxBackend:
    """Builds images and starts :class:`DockerSandbox` containers.

    Args:
        client: a ``docker.DockerClient``; defaults to ``docker.from_env()``.
        docker_cli: executable used for ``docker build``.
        allow_root: permit images whose ``USER`` is root. Off by default so a task cannot
            accidentally grant the agent root inside the container.
        reuse_images: skip the build when an image with the requested tag already exists.
            Tags are content-addressed, so a hit means the build context is unchanged.
            BuildKit assigns a fresh image id on every build, so rebuilding would orphan
            the previous image each run.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        docker_cli: str = "docker",
        allow_root: bool = False,
        reuse_images: bool = True,
    ) -> None:
        try:
            self._client = client if client is not None else docker.from_env()
            self._client.ping()
        except docker.errors.DockerException as exc:
            msg = f"cannot reach the Docker daemon: {exc}"
            raise SandboxError(msg) from exc
        self._docker_cli = docker_cli
        self._allow_root = allow_root
        self._reuse_images = reuse_images

    def build_image(
        self,
        context: Path,
        *,
        tag: str,
        build_args: Mapping[str, str] | None = None,
        timeout: float,
    ) -> ImageRef:
        dockerfile = context / "Dockerfile"
        if not dockerfile.is_file():
            msg = f"no Dockerfile in build context {context}"
            raise BuildError(msg)
        if self._reuse_images and (existing := self._find_image(tag)) is not None:
            log.info("reusing image %s", tag)
            return existing
        cmd = [self._docker_cli, "build", "--progress=plain", "--tag", tag]
        for key, value in (build_args or {}).items():
            cmd += ["--build-arg", f"{key}={value}"]
        cmd.append(str(context))
        log.info("building image %s from %s", tag, context)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "DOCKER_BUILDKIT": "1"},
                check=False,
            )
        except FileNotFoundError as exc:
            msg = f"{self._docker_cli!r} not found on PATH; image builds need the Docker CLI"
            raise BuildError(msg) from exc
        except subprocess.TimeoutExpired as exc:
            msg = f"build of {tag} exceeded {timeout:.0f}s"
            raise BuildError(msg) from exc
        log.debug("build log for %s:\n%s", tag, proc.stderr)
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.splitlines()[-_BUILD_LOG_TAIL_LINES:])
            msg = f"build of {tag} failed with exit code {proc.returncode}:\n{tail}"
            raise BuildError(msg)
        built = self._find_image(tag)
        if built is None:
            msg = f"build of {tag} reported success but the image is missing"
            raise BuildError(msg)
        log.info("built %s (%s) in %.1fs", tag, built.id[7:19], time.monotonic() - started)
        return built

    def _find_image(self, tag: str) -> ImageRef | None:
        try:
            image = self._client.images.get(tag)
        except docker.errors.ImageNotFound:
            return None
        except docker.errors.DockerException as exc:
            msg = f"could not inspect image {tag}: {exc}"
            raise BuildError(msg) from exc
        user = str((image.attrs.get("Config") or {}).get("User") or "")
        return ImageRef(tag=tag, id=str(image.id), user=user)

    @contextmanager
    def start(
        self,
        image: ImageRef,
        *,
        workdir: str,
        limits: ResourceLimits,
    ) -> Iterator[DockerSandbox]:
        self._check_user(image)
        try:
            container = self._client.containers.create(
                image.id,
                entrypoint=["sleep", "infinity"],
                command=[],
                working_dir=workdir,
                network_mode="none",
                nano_cpus=int(limits.cpus * 1_000_000_000),
                mem_limit=limits.memory_bytes,
                memswap_limit=limits.memory_bytes,
                pids_limit=limits.pids_limit,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                init=True,
                labels={MANAGED_LABEL: "true"},
            )
        except docker.errors.DockerException as exc:
            msg = f"could not create container from {image.tag}: {exc}"
            raise SandboxError(msg) from exc
        sandbox = DockerSandbox(self._client, container, workdir=workdir)
        try:
            try:
                container.start()
            except docker.errors.DockerException as exc:
                msg = f"could not start container {container.short_id}: {exc}"
                raise SandboxError(msg) from exc
            log.debug("started container %s from %s", container.short_id, image.tag)
            yield sandbox
        finally:
            sandbox.close()

    def remove_image(self, image: ImageRef) -> None:
        try:
            self._client.images.remove(image.tag)
        except docker.errors.ImageNotFound:
            return
        except docker.errors.DockerException as exc:
            log.warning("could not remove image %s: %s", image.tag, exc)
            return
        log.info("removed image %s", image.tag)

    def _check_user(self, image: ImageRef) -> None:
        if self._allow_root:
            return
        if is_root_user(image.user):
            msg = (
                f"image {image.tag} runs as root; set a non-root USER in its Dockerfile "
                "(or construct the backend with allow_root=True)"
            )
            raise SandboxError(msg)


class DockerSandbox:
    """One running container. Created by :meth:`DockerSandboxBackend.start`."""

    def __init__(self, client: Any, container: Any, *, workdir: str) -> None:
        self._api = client.api
        self._container = container
        self._workdir = workdir
        self._killed = False
        self._identity: tuple[int, int] | None = None

    @property
    def workdir(self) -> str:
        return self._workdir

    def exec(self, command: str, *, timeout: float, workdir: str | None = None) -> ExecResult:
        self._ensure_alive()
        if timeout <= 0:
            self.kill()
            msg = f"no time left to run {command[:120]!r}; sandbox killed"
            raise SandboxTimeoutError(msg)
        started = time.monotonic()
        try:
            exec_id = self._api.exec_create(
                self._container.id,
                ["sh", "-c", command],
                workdir=workdir or self._workdir,
            )["Id"]
        except docker.errors.DockerException as exc:
            msg = f"exec_create failed: {exc}"
            raise SandboxError(msg) from exc

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crucible-exec")
        future: Future[tuple[bytes, bytes]] = pool.submit(self._drain_exec, exec_id)
        try:
            stdout, stderr = future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            self.kill()
            pool.shutdown(wait=False)
            msg = f"command exceeded {timeout:.1f}s and the sandbox was killed: {command[:120]!r}"
            raise SandboxTimeoutError(msg) from exc
        except docker.errors.DockerException as exc:
            pool.shutdown(wait=False)
            msg = f"exec failed: {exc}"
            raise SandboxError(msg) from exc
        pool.shutdown(wait=True)

        try:
            exit_code = int(self._api.exec_inspect(exec_id)["ExitCode"])
        except docker.errors.DockerException as exc:
            msg = f"exec_inspect failed: {exc}"
            raise SandboxError(msg) from exc
        duration = time.monotonic() - started
        log.debug("exec exit=%d in %.2fs: %s", exit_code, duration, command[:200])
        return ExecResult(
            exit_code=exit_code,
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            duration_seconds=duration,
        )

    def _drain_exec(self, exec_id: str) -> tuple[bytes, bytes]:
        """Stream an exec's output, keeping at most ``MAX_OUTPUT_BYTES`` per channel.

        Output beyond the cap is read and discarded so a chatty command cannot exhaust
        host memory, while the process itself keeps running until it exits or times out.
        """
        out, err = bytearray(), bytearray()
        for stdout_chunk, stderr_chunk in self._api.exec_start(exec_id, stream=True, demux=True):
            if stdout_chunk and len(out) < MAX_OUTPUT_BYTES:
                out += stdout_chunk
            if stderr_chunk and len(err) < MAX_OUTPUT_BYTES:
                err += stderr_chunk
        return bytes(out), bytes(err)

    def read_file(self, path: str) -> bytes:
        self._ensure_alive()
        try:
            stream, _stat = self._container.get_archive(path)
        except docker.errors.NotFound as exc:
            msg = f"no such file in sandbox: {path}"
            raise SandboxFileNotFoundError(msg) from exc
        except docker.errors.DockerException as exc:
            msg = f"could not read {path}: {exc}"
            raise SandboxError(msg) from exc
        buffer = io.BytesIO()
        for chunk in stream:
            buffer.write(chunk)
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as tar:
            members = tar.getmembers()
            if len(members) != 1 or not members[0].isfile():
                msg = f"{path} is not a regular file"
                raise SandboxError(msg)
            extracted = tar.extractfile(members[0])
            if extracted is None:  # pragma: no cover - isfile() guarantees a stream
                msg = f"could not extract {path}"
                raise SandboxError(msg)
            return extracted.read()

    def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        self._ensure_alive()
        target = PurePosixPath(path)
        if not target.is_absolute():
            msg = f"write_file needs an absolute path, got {path!r}"
            raise SandboxError(msg)
        result = self.exec(f"mkdir -p {_quote(str(target.parent))}", timeout=_HOUSEKEEPING_TIMEOUT)
        if not result.ok:
            msg = f"could not create directory {target.parent}: {result.stderr.strip()}"
            raise SandboxError(msg)
        uid, gid = self._user_identity()
        info = tarfile.TarInfo(name=target.name)
        info.size = len(content)
        info.mode = mode
        info.uid, info.gid = uid, gid
        info.mtime = int(time.time())
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.addfile(info, io.BytesIO(content))
        self._put_archive(str(target.parent), archive.getvalue())

    def copy_in(self, source: Path, dest: str) -> None:
        self._ensure_alive()
        if not source.exists():
            msg = f"copy_in source does not exist: {source}"
            raise SandboxError(msg)
        target = PurePosixPath(dest)
        uid, gid = self._user_identity()

        def _own(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.uid, info.gid = uid, gid
            info.uname = info.gname = ""
            return info

        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.add(source, arcname=target.name, filter=_own)
        self._put_archive(str(target.parent), archive.getvalue())

    def snapshot(self, dest: Path) -> WorkspaceSnapshot:
        # Works on a killed container too: archives come from the stopped filesystem.
        try:
            stream, _stat = self._container.get_archive(self._workdir)
            with dest.open("wb") as fh:
                for chunk in stream:
                    fh.write(chunk)
        except docker.errors.DockerException as exc:
            msg = f"could not export {self._workdir}: {exc}"
            raise SandboxError(msg) from exc
        return WorkspaceSnapshot(archive=dest, root=self._workdir)

    def restore(self, snapshot: WorkspaceSnapshot) -> None:
        self._ensure_alive()
        # get_archive names the archive root after the directory's basename, so the
        # archive must be extracted into the directory's parent.
        parent = str(PurePosixPath(snapshot.root).parent)
        with snapshot.archive.open("rb") as fh:
            self._put_archive(parent, fh.read())

    def kill(self) -> None:
        if self._killed:
            return
        self._killed = True
        try:
            self._container.kill()
        except docker.errors.DockerException as exc:
            # Already exited or gone; either way the goal (stopped) is met.
            log.debug("kill of %s ignored: %s", self._container.short_id, exc)

    def close(self) -> None:
        """Remove the container and its anonymous volumes. Safe to call repeatedly."""
        self._killed = True
        try:
            self._container.remove(force=True, v=True)
        except docker.errors.NotFound:
            return
        except docker.errors.DockerException as exc:
            log.warning("could not remove container %s: %s", self._container.short_id, exc)
            return
        log.debug("removed container %s", self._container.short_id)

    def _ensure_alive(self) -> None:
        if self._killed:
            msg = "sandbox has been killed"
            raise SandboxError(msg)

    def _user_identity(self) -> tuple[int, int]:
        if self._identity is None:
            result = self.exec("id -u && id -g", timeout=_HOUSEKEEPING_TIMEOUT)
            if not result.ok:
                msg = f"could not determine sandbox user: {result.stderr.strip()}"
                raise SandboxError(msg)
            uid, gid = (int(part) for part in result.stdout.split())
            self._identity = (uid, gid)
        return self._identity

    def _put_archive(self, parent: str, data: bytes) -> None:
        try:
            self._container.put_archive(parent, data)
        except docker.errors.DockerException as exc:
            msg = f"could not copy files into {parent}: {exc}"
            raise SandboxError(msg) from exc


def is_root_user(user: str) -> bool:
    """True if a Dockerfile ``USER`` value (``name[:group]`` or ``uid[:gid]``) means root."""
    name = user.split(":", 1)[0]
    return name in ("", "root", "0")


def _decode(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(data) >= MAX_OUTPUT_BYTES:
        text += _TRUNCATED_MARKER
    return text


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
