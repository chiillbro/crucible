"""Deadline helper tests with an injected clock."""

from __future__ import annotations

from crucible.deadline import Deadline


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_remaining_counts_down_and_floors_at_zero() -> None:
    clock = FakeClock()
    deadline = Deadline.after(10, clock=clock)
    assert deadline.remaining() == 10
    assert not deadline.expired()
    clock.now += 4
    assert deadline.remaining() == 6
    clock.now += 100
    assert deadline.remaining() == 0
    assert deadline.expired()


def test_clamp_takes_the_smaller_budget() -> None:
    clock = FakeClock()
    deadline = Deadline.after(5, clock=clock)
    assert deadline.clamp(60) == 5
    assert deadline.clamp(2) == 2
    clock.now += 5
    assert deadline.clamp(2) == 0
