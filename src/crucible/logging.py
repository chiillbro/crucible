"""Logging setup. Diagnostics go to stderr through ``logging``; stdout is for results."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_NOISY_LOGGERS = ("urllib3", "docker", "httpx", "httpcore", "anthropic")


def configure_logging(level: str) -> None:
    """Configure the root logger once. Third-party loggers stay at WARNING unless DEBUG."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        msg = f"unknown log level {level!r}"
        raise ValueError(msg)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(
            min(logging.WARNING, numeric) if numeric <= logging.DEBUG else logging.WARNING
        )
