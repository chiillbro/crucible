"""Plain-text summaries for stdout."""

from __future__ import annotations

from crucible.domain.calibration import Calibration
from crucible.domain.schema import Score, TaskResult


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _pass_at_k_part(score: Score) -> str:
    # pass@1 is always shown; only add the pass@k column when k differs.
    if score.k == 1:
        return ""
    return f"pass@{score.k}: {_fmt(score.pass_at_k)}  "


def format_summary(result: TaskResult) -> str:
    score = result.score
    lines = [
        f"task:   {result.task_name}  ({result.task_dir})",
        f"agent:  {result.agent}",
        (
            f"trials: {score.n}  passed: {score.c}  pass@1: {_fmt(score.pass_at_1)}  "
            f"{_pass_at_k_part(score)}mean reward: {_fmt(score.mean_reward)}"
        ),
        "",
        f"{'#':>3}  {'status':<15}{'reward':>6}  {'time':>7}  reason",
    ]
    for trial in result.trials:
        reason = (trial.reason or "").splitlines()[0] if trial.reason else ""
        lines.append(
            f"{trial.index:>3}  {trial.status.value:<15}{trial.reward:>6}  "
            f"{trial.duration_seconds:>6.1f}s  {reason[:80]}"
        )
    if score.pass_at_k is None and score.n:
        lines.append("")
        lines.append(
            f"note: pass@{score.k} is undefined with {score.n} trial(s); run at least {score.k}."
        )
    return "\n".join(lines)


def format_calibration(calibration: Calibration) -> str:
    oracle, nop = calibration.oracle, calibration.nop
    lines = [
        f"calibration for {oracle.task_name}",
        f"  oracle: {oracle.score.c}/{oracle.score.n} passed  (required: all)",
        f"  nop:    {nop.score.c}/{nop.score.n} passed  (required: none)",
    ]
    problems = calibration.problems()
    if problems:
        lines.append("  result: NOT CALIBRATED")
        lines.extend(f"    - {problem}" for problem in problems)
    else:
        lines.append("  result: calibrated")
    return "\n".join(lines)
