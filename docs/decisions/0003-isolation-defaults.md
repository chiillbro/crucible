# 0003. Isolation defaults

## Context
The agent's container runs untrusted, model-generated commands. Reproducibility also
requires that trials do not depend on the outside world.

## Decision
Every sandbox runs with `network_mode=none`, explicit CPU, memory (swap disabled) and PID
limits from `task.toml`, all capabilities dropped, `no-new-privileges`, `init` as PID 1, and
the image's non-root `USER`, which the harness verifies before starting. A hard wall-clock
budget kills the container when exceeded. Containers are always removed in a `finally`.

## Consequences
Tasks that need the network (package installs, API calls) are out of scope for now; they
need an explicit allowlist design and are on the roadmap. Images must declare a non-root
user; `DockerSandboxBackend(allow_root=True)` exists for deliberate exceptions. Dropping all
capabilities means even root inside the container cannot bypass file permissions, which is
why the verifier cleans the workdir at image-build time rather than at run time (see 0005).
