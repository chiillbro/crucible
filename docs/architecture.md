# Architecture

Crucible is a hexagonal application: a small core that knows about tasks, trials and
scores, three ports it depends on, and adapters that bind those ports to Docker, pytest
and the Claude API. Nothing in `crucible.runner` or `crucible.domain` imports Docker or an
LLM SDK.

## Module map

```
src/crucible/
  cli.py                 Typer app: run, validate. Composes adapters into a Runner.
  config.py              Settings (pydantic-settings): API key, model, log level.
  runner.py              Runner: build -> trials -> score. Depends only on ports.
  report.py              Plain-text summaries for stdout.
  logging.py             stdlib logging setup; stdout is reserved for results.
  errors.py              CrucibleError hierarchy.
  deadline.py            Monotonic deadline shared by runner, agents and sandboxes.
  images.py              Content-addressed image tags.
  domain/
    schema.py            Task schema + loader, TrialResult, TaskResult, Score, Verdict.
    scoring.py           pass@k (pure).
    calibration.py       oracle-all-pass / nop-all-fail rule (pure).
  ports/
    sandbox.py           SandboxBackend, Sandbox, ImageRef, ExecResult, WorkspaceSnapshot.
    agent.py             Agent.
    verifier.py          Verifier.
  adapters/
    docker_sandbox.py    DockerSandboxBackend, DockerSandbox.
    pytest_verifier.py   PytestVerifier.
    agents/              OracleAgent, NopAgent, LlmAgent.
```

Ports are `typing.Protocol`s. Adapters do not inherit from them; they merely have the
right shape, which keeps test fakes (`tests/fakes.py`) trivial.

## One run

```
Runner.run(task, trials=N, k=K)
  environment = backend.build_image(task.environment_dir)          # crucible/<task>-env:<digest>
  verifier.prepare(task, environment)                              # builds verifier-base and verifier
  for i in range(N):
      _run_trial(task, environment, i)                             # never raises
  score_trials(rewards, K) -> TaskResult
```

A build failure raises `BuildError` and aborts the run: no trial could be meaningful.
Everything after that is recorded, not raised.

## One trial

```
with backend.start(environment, workdir, limits) as sandbox:      # container removed on exit
    outcome = agent.solve(sandbox, task, Deadline.after(agent.timeout_seconds))
    snapshot = sandbox.snapshot(tmp / "workspace.tar")            # tar of the workdir
verdict = verifier.verify(task, snapshot)
```

`PytestVerifier.verify` starts a container from the verifier image, asserts the workdir is
absent or empty, restores the snapshot into it, and runs

```
python -m pytest -q -rA -p no:cacheprovider --rootdir=/tests --confcutdir=/tests /tests
```

### Status mapping

| What happened | `status` | `reward` |
| --- | --- | --- |
| pytest exit 0 | `passed` | 1 |
| pytest exit 1 or 2 (failures, collection errors), or verifier timeout | `failed` | 0 |
| agent exceeded its budget, or a sandbox command hit its deadline | `agent_timeout` | 0 |
| `AgentError` or any unexpected exception from the agent | `agent_error` | 0 |
| `SandboxError` while starting or using the agent's sandbox | `sandbox_error` | 0 |
| `VerifierError` (no tests collected, pytest usage/internal error) or verifier container failure | `verifier_error` | 0 |

A verifier timeout counts as `failed` rather than an error on purpose: a solution that makes
the tests hang has not solved the task, and the harness cannot tell that apart from a slow
test suite. Raise `[verifier].timeout_seconds` if a task legitimately needs more.

## Timeouts and threads

Two layers enforce the solve budget:

1. **Cooperative.** The agent receives a `Deadline`. `LlmAgent` checks it before each model
   call and clamps every `bash` call's timeout to the time remaining. `DockerSandbox.exec`
   runs each command in a worker thread and, if it outlives its timeout, kills the container
   and raises `SandboxTimeoutError`; the sandbox is dead from then on.
2. **Hard.** `Runner._solve` runs `agent.solve` in a worker thread and waits at most
   `timeout_seconds`. If the agent is still running, the sandbox is killed, which unblocks any
   in-flight command, and the trial is recorded as `agent_timeout`.

Build timeouts use `subprocess.run(timeout=...)`, which kills the `docker build` process.

## Image lifecycle

Tags are `crucible/<task>-<role>:<digest>`, where the digest covers the build context (and,
for derived images, the parent's image id). `DockerSandboxBackend` reuses an existing tag
instead of rebuilding; `--rebuild` forces a build and `--rm-images` removes everything the
run built when it finishes. Containers are always removed in a `finally`.

## Extension points

- **New sandbox backend:** implement `SandboxBackend` and `Sandbox`; the runner and verifier
  never touch Docker types.
- **New verifier:** implement `prepare` and `verify`. The `WorkspaceSnapshot` contract (a tar
  of the workdir plus its root path) is language-agnostic.
- **New agent:** implement `solve(sandbox, task, deadline)`; use only `task.instruction`,
  `task.workdir` and the sandbox unless you are a privileged agent like the oracle.
- **Parallel trials:** `_run_trial` is self-contained per trial; a future runner can fan it
  out across a pool without touching adapters.
