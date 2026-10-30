"""Verifier port: grade an agent's resulting workspace."""

from __future__ import annotations

from typing import Protocol

from crucible.domain.schema import Task, Verdict
from crucible.ports.sandbox import ImageRef, WorkspaceSnapshot


class Verifier(Protocol):
    """Turns a workspace snapshot into a binary verdict.

    ``prepare`` runs once per task before any trial so the hidden tests can be baked into
    an image the agent never sees. ``verify`` must be a pure function of the snapshot and
    the task: no clock, no network, no randomness.
    """

    def prepare(self, task: Task, environment: ImageRef) -> tuple[ImageRef, ...]:
        """Build whatever the verifier needs on top of ``environment``.

        Returns every image it built, so the run can record and remove them.

        Raises:
            BuildError: the verifier image could not be built.
        """
        ...

    def verify(self, task: Task, snapshot: WorkspaceSnapshot) -> Verdict:
        """Grade ``snapshot``. Failing tests are a verdict, not an exception.

        Raises:
            VerifierError: the verifier itself could not run (misconfiguration, no tests).
            SandboxError: the verifier container failed.
        """
        ...
