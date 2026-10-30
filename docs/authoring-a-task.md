# Authoring a task

A task is a directory. Copy `tasks/fix-config-parser` and edit it, or create the files below
from scratch. Run `crucible validate <dir>` until it says `calibrated`.

## 1. Layout

```
my-task/
  task.toml
  instruction.md
  environment/Dockerfile      (+ any files it copies)
  solution/solve.sh           (+ any files it needs)
  tests/Dockerfile
  tests/conftest.py
  tests/test_*.py
```

All five named files are required; `crucible` refuses to load a task without them.

## 2. The environment image

- Start from any image, install what the project needs, copy the broken codebase into the
  workdir (default `/workspace`), set `WORKDIR` and **a non-root `USER`**. The harness refuses
  to start a container whose image runs as root.
- Everything the agent is allowed to see goes here: the code, visible tests, a README.
  Visible tests that pass on the broken code are a good idea; they tell the agent how the
  project is tested and act as regression guards in the hidden suite.
- There is no network during the solve phase, so install every dependency at build time.
- Do not put the hidden tests or the solution anywhere in this image.

## 3. The instruction

`instruction.md` is sent to the agent verbatim as its first user message. Describe the
observed problem precisely enough that the hidden tests are a fair reading of it, and say
which behaviour must be preserved. Avoid describing the fix.

## 4. The reference solution

`solution/solve.sh` is copied to `/solution` inside the agent's sandbox and run with
`sh`, with the task workdir as the current directory and the same limits and no-network
rule the agent has. It must leave the workdir in a passing state. Only the workdir is
graded, so a solution that installs packages or writes outside the workdir will not be
captured.

Keep it boring: copying a fixed file over the broken one (as the example does) is
deterministic and easy to review.

## 5. The hidden tests and the verifier image

`tests/Dockerfile` is built with the build argument `BASE_IMAGE`, which the harness sets to
the *verifier base*: your environment image with the workdir deleted. The minimal and
recommended Dockerfile is

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY . /tests
```

Rules the verifier relies on:

- Tests must live under `/tests`; the harness runs `python -m pytest /tests` with rootdir
  and confcutdir pinned there. If pytest collects nothing, the trial is a `verifier_error`.
- The workdir is **not** on `PYTHONPATH`. Import the code under test by putting the workdir
  on `sys.path` in `tests/conftest.py` (see the example), or by installing the project in
  the environment image with `pip install -e`.
- Do not write into the workdir from `tests/Dockerfile`; `verify` checks it is empty before
  restoring the agent's snapshot and fails the trial otherwise.
- Tests must be deterministic: no clock, no network, no randomness. The verifier container
  has no network either.
- pytest exit code 0 is reward 1. Exit codes 1 and 2 (failures, collection errors caused by
  the agent breaking an import) are reward 0. Other exit codes are verifier errors.

Include tests for the behaviour the instruction describes **and** for behaviour that must
not regress. The nop agent proves the former fail by default; the oracle proves the whole
suite is satisfiable.

## 6. Limits

Set `[agent].timeout_seconds` generously for LLM agents (they read before they edit) and
`[verifier].timeout_seconds` to a few multiples of the suite's normal runtime; a hanging
suite is recorded as a failed trial, not an error.

## 7. Calibrate

```sh
uv run crucible validate my-task --trials 3
```

Exit code 0 and `result: calibrated` means the oracle passed every trial and nop failed every
trial. Any other outcome lists the offending trials with their status and reason. Look at
`runs/my-task/validate/<timestamp>/{oracle,nop}/trial-*/verifier.txt` for the full pytest
output.

## Checklist

- [ ] `USER` in `environment/Dockerfile` is not root
- [ ] no hidden tests or solution in the environment image
- [ ] `solve.sh` only changes the workdir
- [ ] `tests/Dockerfile` starts with `ARG BASE_IMAGE` / `FROM ${BASE_IMAGE}` and copies to `/tests`
- [ ] `tests/conftest.py` (or an editable install) makes the workdir importable
- [ ] `crucible validate` reports `calibrated`
