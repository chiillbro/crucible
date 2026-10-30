"""pass@k scoring. Pure functions, no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from math import comb

from crucible.domain.schema import Score


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021, "Evaluating LLMs Trained on Code").

    Given ``n`` trials of which ``c`` passed, returns the probability that at least one of
    ``k`` trials drawn without replacement passes: ``1 - C(n-c, k) / C(n, k)``.

    Raises:
        ValueError: if ``n < 1``, ``k < 1``, ``k > n`` or ``c`` is outside ``[0, n]``.
    """
    if n < 1:
        msg = f"n must be at least 1, got {n}"
        raise ValueError(msg)
    if k < 1:
        msg = f"k must be at least 1, got {k}"
        raise ValueError(msg)
    if k > n:
        msg = f"k ({k}) cannot exceed n ({n})"
        raise ValueError(msg)
    if not 0 <= c <= n:
        msg = f"c must be in [0, n]; got c={c}, n={n}"
        raise ValueError(msg)
    if n - c < k:
        return 1.0
    # Exact rational arithmetic; convert to float once at the end.
    return float(1 - Fraction(comb(n - c, k), comb(n, k)))


def score_trials(rewards: Sequence[int], k: int) -> Score:
    """Summarise binary trial rewards into a :class:`Score`.

    Undefined statistics (no trials, or ``k > n``) are reported as ``None`` rather than
    guessed, so a caller can never mistake "not measurable" for a number.

    Raises:
        ValueError: if ``k < 1`` or any reward is not 0 or 1.
    """
    if k < 1:
        msg = f"k must be at least 1, got {k}"
        raise ValueError(msg)
    invalid = [r for r in rewards if r not in (0, 1)]
    if invalid:
        msg = f"rewards must be 0 or 1, got {invalid}"
        raise ValueError(msg)
    n = len(rewards)
    c = sum(rewards)
    if n == 0:
        return Score(n=0, c=0, k=k, pass_at_k=None, pass_at_1=None, mean_reward=None)
    return Score(
        n=n,
        c=c,
        k=k,
        pass_at_k=pass_at_k(n, c, k) if k <= n else None,
        pass_at_1=pass_at_k(n, c, 1),
        mean_reward=c / n,
    )
