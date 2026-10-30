"""Calibration rules over TaskResults."""

from __future__ import annotations

from datetime import UTC, datetime

from crucible.domain.calibration import Calibration
from crucible.domain.schema import Score, TaskResult, TrialResult, TrialStatus
from crucible.domain.scoring import score_trials


def _result(
    agent: str, rewards: list[int], statuses: list[TrialStatus] | None = None
) -> TaskResult:
    statuses = statuses or [TrialStatus.PASSED if r else TrialStatus.FAILED for r in rewards]
    now = datetime.now(UTC)
    return TaskResult(
        crucible_version="test",
        task_name="t",
        task_dir="/t",
        agent=agent,
        started_at=now,
        finished_at=now,
        images={},
        trials=[
            TrialResult(
                index=i, status=s, reward=r, duration_seconds=1.0, reason=None if r else "x"
            )
            for i, (r, s) in enumerate(zip(rewards, statuses, strict=True))
        ],
        score=score_trials(rewards, 1)
        if rewards
        else Score(n=0, c=0, k=1, pass_at_k=None, pass_at_1=None, mean_reward=None),
    )


def test_calibrated_when_oracle_all_pass_and_nop_all_fail() -> None:
    calibration = Calibration(oracle=_result("oracle", [1, 1, 1]), nop=_result("nop", [0, 0, 0]))
    assert calibration.ok
    assert calibration.problems() == []


def test_oracle_failure_is_reported_with_status_and_reason() -> None:
    oracle = _result("oracle", [1, 0], [TrialStatus.PASSED, TrialStatus.AGENT_ERROR])
    calibration = Calibration(oracle=oracle, nop=_result("nop", [0]))
    assert not calibration.ok
    assert calibration.problems() == ["oracle trial 1 scored 0 (agent_error): x"]


def test_nop_pass_is_reported() -> None:
    calibration = Calibration(oracle=_result("oracle", [1]), nop=_result("nop", [0, 1]))
    assert calibration.problems() == ["nop trial 1 scored 1: hidden tests pass without any change"]


def test_empty_runs_are_not_calibrated() -> None:
    calibration = Calibration(oracle=_result("oracle", []), nop=_result("nop", []))
    assert calibration.problems() == ["oracle ran no trials", "nop ran no trials"]
