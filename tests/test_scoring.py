"""Scorer unit tests: pass@k estimator and trial summarisation."""

from __future__ import annotations

from math import comb

import pytest

from crucible.domain.scoring import pass_at_k, score_trials


@pytest.mark.parametrize(
    ("n", "c", "k", "expected"),
    [
        (1, 1, 1, 1.0),
        (1, 0, 1, 0.0),
        (10, 3, 1, 0.3),
        (10, 10, 1, 1.0),
        (10, 0, 5, 0.0),
        (10, 1, 5, 0.5),  # 1 - C(9,5)/C(10,5) = 1 - 126/252
        (10, 1, 10, 1.0),  # n - c < k: at least one of the k draws must pass
        (4, 2, 3, 1.0),  # n - c = 2 < 3
        (4, 2, 2, 1 - comb(2, 2) / comb(4, 2)),
    ],
)
def test_pass_at_k_known_values(n: int, c: int, k: int, expected: float) -> None:
    assert pass_at_k(n, c, k) == pytest.approx(expected)


def test_pass_at_1_equals_pass_rate() -> None:
    for n in range(1, 30):
        for c in range(n + 1):
            assert pass_at_k(n, c, 1) == pytest.approx(c / n)


def test_pass_at_k_is_monotonic_in_k() -> None:
    n, c = 20, 4
    values = [pass_at_k(n, c, k) for k in range(1, n + 1)]
    assert values == sorted(values)
    assert values[-1] == 1.0


def test_pass_at_k_exact_for_large_n() -> None:
    # Exercise the exact-rational path where naive floats would accumulate error.
    assert pass_at_k(1000, 500, 1) == pytest.approx(0.5)
    assert pass_at_k(1000, 1, 1000) == 1.0


@pytest.mark.parametrize(
    ("n", "c", "k", "match"),
    [
        (0, 0, 1, "n must be at least 1"),
        (5, 0, 0, "k must be at least 1"),
        (5, 0, 6, "cannot exceed n"),
        (5, 6, 1, r"c must be in \[0, n\]"),
        (5, -1, 1, r"c must be in \[0, n\]"),
    ],
)
def test_pass_at_k_rejects_invalid_input(n: int, c: int, k: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        pass_at_k(n, c, k)


def test_score_zero_trials_is_undefined() -> None:
    score = score_trials([], k=1)
    assert score.n == 0
    assert score.c == 0
    assert score.pass_at_k is None
    assert score.pass_at_1 is None
    assert score.mean_reward is None


def test_score_all_pass() -> None:
    score = score_trials([1, 1, 1], k=2)
    assert (score.n, score.c, score.k) == (3, 3, 2)
    assert score.pass_at_k == 1.0
    assert score.pass_at_1 == 1.0
    assert score.mean_reward == 1.0


def test_score_all_fail() -> None:
    score = score_trials([0, 0, 0, 0], k=4)
    assert score.pass_at_k == 0.0
    assert score.pass_at_1 == 0.0
    assert score.mean_reward == 0.0


def test_score_mixed() -> None:
    score = score_trials([1, 0, 0, 1, 0], k=2)
    assert score.c == 2
    assert score.mean_reward == pytest.approx(0.4)
    assert score.pass_at_1 == pytest.approx(0.4)
    assert score.pass_at_k == pytest.approx(1 - comb(3, 2) / comb(5, 2))


def test_score_k_greater_than_n_leaves_pass_at_k_undefined() -> None:
    score = score_trials([1, 0], k=5)
    assert score.pass_at_k is None
    assert score.pass_at_1 == pytest.approx(0.5)
    assert score.mean_reward == pytest.approx(0.5)


def test_score_rejects_invalid_k() -> None:
    with pytest.raises(ValueError, match="k must be at least 1"):
        score_trials([1], k=0)


def test_score_rejects_non_binary_rewards() -> None:
    with pytest.raises(ValueError, match="rewards must be 0 or 1"):
        score_trials([1, 2], k=1)
