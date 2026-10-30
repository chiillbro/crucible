# 0001. Python 3.11+

## Context
The harness talks to Docker, runs pytest, validates declarative config and calls an LLM
API. Comparable harnesses (SWE-bench, Terminal-Bench, inspect-ai) are Python, as are the
task authors we expect.

## Decision
Python 3.11 or newer, managed with uv, with a committed lockfile. Full type hints checked by
mypy in strict mode, including tests.

## Consequences
Runtime speed is irrelevant: the work is I/O- and container-bound. We get first-class
Docker, Pydantic and pytest tooling, `tomllib` in the standard library, and a codebase task
authors can read. The cost is a heavier interpreter in CI than a compiled tool would need.
