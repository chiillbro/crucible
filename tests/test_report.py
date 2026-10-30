"""Summary formatting."""

from __future__ import annotations

from crucible.domain.calibration import Calibration
from crucible.report import format_calibration, format_summary
from tests.test_calibration import _result


def test_summary_lists_scores_and_trials() -> None:
    result = _result("oracle", [1, 0])
    text = format_summary(result)
    assert "task:   t  (/t)" in text
    assert "agent:  oracle" in text
    assert "trials: 2  passed: 1  pass@1: 0.500  pass@1: 0.500  mean reward: 0.500" in text
    assert "  0  passed              1" in text
    assert "  1  failed              0" in text
    assert text.rstrip().endswith("x")
    assert "undefined" not in text


def test_summary_notes_undefined_pass_at_k() -> None:
    result = _result("oracle", [1]).model_copy(
        update={
            "score": _result("oracle", [1]).score.model_copy(update={"k": 5, "pass_at_k": None})
        }
    )
    text = format_summary(result)
    assert "pass@5: n/a" in text
    assert "pass@5 is undefined with 1 trial(s); run at least 5." in text


def test_calibration_report() -> None:
    good = Calibration(oracle=_result("oracle", [1, 1]), nop=_result("nop", [0, 0]))
    assert format_calibration(good).splitlines() == [
        "calibration for t",
        "  oracle: 2/2 passed  (required: all)",
        "  nop:    0/2 passed  (required: none)",
        "  result: calibrated",
    ]
    bad = Calibration(oracle=_result("oracle", [1, 1]), nop=_result("nop", [1]))
    text = format_calibration(bad)
    assert "  result: NOT CALIBRATED" in text
    assert "    - nop trial 0 scored 1" in text
