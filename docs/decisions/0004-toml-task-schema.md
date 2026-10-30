# 0004. TOML task files validated by Pydantic

## Context
Tasks are authored and reviewed by people, diffed in pull requests, and must fail loudly
when malformed. Terminal-Bench established `task.toml` plus conventional file names as a
familiar shape.

## Decision
A task is a directory with fixed file names and a `task.toml` parsed by `tomllib` and
validated by frozen Pydantic v2 models with `extra="forbid"`. Timeouts, limits and the
workdir are validated (positive numbers, Docker memory syntax, absolute workdir). Loading
also checks that every required file exists and the instruction is non-empty.

## Consequences
Declarative and diff-friendly, at the cost of expressiveness: a task cannot compute its
own configuration. Unknown keys are errors, so typos surface immediately rather than as a
silently default limit. Schema changes are explicit, versioned code changes.
