"""Logging configuration."""

from __future__ import annotations

import logging

import pytest

from crucible.logging import configure_logging


def test_configure_sets_level_and_single_stderr_handler() -> None:
    configure_logging("debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert logging.getLogger("urllib3").level == logging.WARNING
    configure_logging("INFO")
    assert root.level == logging.INFO
    assert len(root.handlers) == 1


def test_unknown_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown log level 'LOUD'"):
        configure_logging("LOUD")
