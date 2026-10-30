"""Sandbox port: build images, run isolated containers, move files in and out."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from crucible.domain.schema import ResourceLimits


@dataclass(frozen=True)
class ImageRef:
    """A built image: the tag Crucible assigned and the backend's content identifier."""

    tag: str
    id: str


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one command run inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """The agent's resulting workspace, exported from the sandbox as a tar archive.

    ``archive`` is a host path; ``root`` is the container path the archive was taken
    from. Extracting the archive at ``/`` recreates ``root`` exactly as the agent left it.
    """

    archive: Path
    root: str


class Sandbox(Protocol):
    """A running, isolated environment for one trial.

    Commands run as the image's configured (non-root) user with no network access.
    Once a command exceeds its timeout the sandbox is killed and every further call
    raises :class:`~crucible.errors.SandboxError`.
    """

    @property
    def workdir(self) -> str: ...

    def exec(self, command: str, *, timeout: float, workdir: str | None = None) -> ExecResult:
        """Run ``command`` with ``sh -c``.

        Raises:
            SandboxTimeoutError: the command did not finish within ``timeout`` seconds.
            SandboxError: the sandbox is unusable or the backend failed.
        """
        ...

    def read_file(self, path: str) -> bytes:
        """Raises :class:`~crucible.errors.SandboxFileNotFoundError` if ``path`` is missing."""
        ...

    def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        """Write ``content`` to ``path``, creating parent directories as needed."""
        ...

    def copy_in(self, source: Path, dest: str) -> None:
        """Copy a host file or directory tree to ``dest`` inside the sandbox."""
        ...

    def snapshot(self, dest: Path) -> WorkspaceSnapshot:
        """Export the workspace directory to a tar archive at host path ``dest``."""
        ...

    def kill(self) -> None:
        """Stop the sandbox immediately. Idempotent."""
        ...


class SandboxBackend(Protocol):
    """Builds images and starts sandboxes. One instance serves a whole run."""

    def build_image(
        self,
        context: Path,
        *,
        tag: str,
        build_args: Mapping[str, str] | None = None,
        timeout: float,
    ) -> ImageRef:
        """Build ``context/Dockerfile`` and tag it.

        Raises:
            BuildError: the build failed or exceeded ``timeout`` seconds.
        """
        ...

    def start(
        self,
        image: ImageRef,
        *,
        workdir: str,
        limits: ResourceLimits,
        scratch_mounts: tuple[str, ...] = (),
    ) -> AbstractContextManager[Sandbox]:
        """Start a sandbox from ``image``; leaving the context always removes it.

        ``scratch_mounts`` are container paths that are masked with an empty, writable
        in-memory filesystem, hiding whatever the image holds there.

        Raises:
            SandboxError: the container could not be created or started.
        """
        ...

    def remove_image(self, image: ImageRef) -> None:
        """Delete a built image. Missing images are not an error."""
        ...
