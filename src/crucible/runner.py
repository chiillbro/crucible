"""Run orchestration: build -> trials (solve, snapshot, verify) -> score.

The runner knows nothing about Docker, pytest or LLMs; it receives a backend, an agent
and a verifier through the constructor and only talks to their ports.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from pathlib import Path

from crucible import __version__
from crucible.deadline import Deadline
from crucible.domain.schema import (
    AgentOutcome,
    Task,
    TaskResult,
    TrialResult,
    TrialStatus,
    Verdict,
)
from crucible.domain.scoring import score_trials
from crucible.errors import (
    AgentError,
    AgentTimeoutError,
    CrucibleError,
    SandboxError,
    SandboxTimeoutError,
    VerifierError,
)
from crucible.images import IMAGE_NAMESPACE, content_digest, image_tag
from crucible.ports import Agent, ImageRef, Sandbox, SandboxBackend, Verifier

log = logging.getLogger(__name__)


class Runner:
    """Evaluates one task with one agent over N trials.

    Args:
        backend: builds images and starts sandboxes.
        agent: acts inside each trial's sandbox.
        verifier: grades each trial's workspace snapshot.
        artifacts_dir: if set, per-trial verifier output and results are written under it.
        remove_images: delete the images this run built when it finishes. Off by default
            because tags are content-addressed and rebuilding is the main cost of a run.
    """

    def __init__(
        self,
        *,
        backend: SandboxBackend,
        agent: Agent,
        verifier: Verifier,
        artifacts_dir: Path | None = None,
        remove_images: bool = False,
    ) -> None:
        self._backend = backend
        self._agent = agent
        self._verifier = verifier
        self._artifacts_dir = artifacts_dir
        self._remove_images = remove_images

    def run(self, task: Task, *, trials: int, k: int) -> TaskResult:
        """Build, run ``trials`` trials and score them.

        Trial-level failures are recorded as reward 0 with a reason. Build failures
        propagate as :class:`~crucible.errors.BuildError` since no trial can run.

        Raises:
            ValueError: if ``trials`` or ``k`` is below 1.
        """
        if trials < 1:
            msg = f"trials must be at least 1, got {trials}"
            raise ValueError(msg)
        if k < 1:
            msg = f"k must be at least 1, got {k}"
            raise ValueError(msg)
        started_at = datetime.now(UTC)
        built: list[ImageRef] = []
        try:
            environment = self._build_environment(task)
            built.append(environment)
            built.extend(self._verifier.prepare(task, environment))
            results = [self._run_trial(task, environment, index) for index in range(trials)]
        finally:
            if self._remove_images:
                for image in reversed(built):
                    self._backend.remove_image(image)
        score = score_trials([trial.reward for trial in results], k)
        log.info(
            "%s on %s: %d/%d passed, pass@%d=%s",
            self._agent.name,
            task.name,
            score.c,
            score.n,
            k,
            "undefined" if score.pass_at_k is None else f"{score.pass_at_k:.3f}",
        )
        return TaskResult(
            crucible_version=__version__,
            task_name=task.name,
            task_dir=str(task.root),
            agent=self._agent.name,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            images={_role_of(image.tag, task.name): image.tag for image in built},
            trials=results,
            score=score,
        )

    def _build_environment(self, task: Task) -> ImageRef:
        return self._backend.build_image(
            task.environment_dir,
            tag=image_tag(task.name, "env", content_digest(task.environment_dir)),
            timeout=task.config.environment.build_timeout_seconds,
        )

    def _run_trial(self, task: Task, environment: ImageRef, index: int) -> TrialResult:
        """Run one trial. Never raises; every failure becomes a recorded result."""
        log.info("trial %d: starting %s", index, self._agent.name)
        started = time.monotonic()
        outcome: AgentOutcome | None = None
        with tempfile.TemporaryDirectory(prefix=f"crucible-trial-{index}-") as tmp:
            try:
                with self._backend.start(
                    environment, workdir=task.workdir, limits=task.limits
                ) as sandbox:
                    outcome = self._solve(sandbox, task)
                    snapshot = sandbox.snapshot(Path(tmp) / "workspace.tar")
            except (AgentTimeoutError, SandboxTimeoutError) as exc:
                return self._record(index, started, TrialStatus.AGENT_TIMEOUT, str(exc))
            except AgentError as exc:
                return self._record(index, started, TrialStatus.AGENT_ERROR, str(exc))
            except SandboxError as exc:
                return self._record(index, started, TrialStatus.SANDBOX_ERROR, str(exc))

            try:
                verdict = self._verifier.verify(task, snapshot)
            except (VerifierError, SandboxError) as exc:
                return self._record(
                    index, started, TrialStatus.VERIFIER_ERROR, str(exc), outcome=outcome
                )
            except Exception as exc:
                log.exception("trial %d: verifier crashed", index)
                return self._record(
                    index,
                    started,
                    TrialStatus.VERIFIER_ERROR,
                    f"verifier crashed: {exc!r}",
                    outcome=outcome,
                )

        if verdict.passed:
            return self._record(index, started, TrialStatus.PASSED, None, outcome, verdict)
        reason = (
            verdict.output
            if verdict.exit_code is None
            else f"hidden tests failed (pytest exit {verdict.exit_code})"
        )
        return self._record(index, started, TrialStatus.FAILED, reason, outcome, verdict)

    def _solve(self, sandbox: Sandbox, task: Task) -> AgentOutcome:
        """Run the agent under a hard wall-clock budget.

        The agent gets a cooperative :class:`Deadline`; if it is still running when the
        budget ends, the sandbox is killed, which unblocks any in-flight command.
        """
        budget = task.config.agent.timeout_seconds
        deadline = Deadline.after(budget)
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crucible-agent")
        future = pool.submit(self._agent.solve, sandbox, task, deadline)
        try:
            outcome = future.result(timeout=budget)
        except FutureTimeoutError as exc:
            sandbox.kill()
            pool.shutdown(wait=False)
            msg = f"agent exceeded its {budget:.0f}s budget; sandbox killed"
            raise AgentTimeoutError(msg) from exc
        except CrucibleError:
            pool.shutdown(wait=False)
            raise
        except Exception as exc:
            pool.shutdown(wait=False)
            log.exception("agent %s crashed", self._agent.name)
            msg = f"agent crashed: {exc!r}"
            raise AgentError(msg) from exc
        pool.shutdown(wait=True)
        return outcome

    def _record(
        self,
        index: int,
        started: float,
        status: TrialStatus,
        reason: str | None,
        outcome: AgentOutcome | None = None,
        verdict: Verdict | None = None,
    ) -> TrialResult:
        result = TrialResult(
            index=index,
            status=status,
            reward=1 if status is TrialStatus.PASSED else 0,
            reason=reason,
            duration_seconds=time.monotonic() - started,
            agent=outcome,
            verdict=verdict,
        )
        level = (
            logging.INFO if status in (TrialStatus.PASSED, TrialStatus.FAILED) else logging.WARNING
        )
        log.log(
            level,
            "trial %d: %s (%.1fs)%s",
            index,
            status.value,
            result.duration_seconds,
            f": {reason}" if reason else "",
        )
        self._write_artifacts(result)
        return result

    def _write_artifacts(self, result: TrialResult) -> None:
        if self._artifacts_dir is None:
            return
        trial_dir = self._artifacts_dir / f"trial-{result.index:02d}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        (trial_dir / "trial.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        if result.verdict is not None:
            (trial_dir / "verifier.txt").write_text(result.verdict.output, encoding="utf-8")


def _role_of(tag: str, task_name: str) -> str:
    """``crucible/<task>-<role>:<digest>`` -> ``<role>``."""
    prefix = f"{IMAGE_NAMESPACE}/{task_name}-"
    name = tag.split(":", 1)[0]
    return name.removeprefix(prefix) if name.startswith(prefix) else name


def dump_result(result: TaskResult, path: Path) -> None:
    """Write ``results.json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
