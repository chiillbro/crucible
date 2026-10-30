# 0010. Hexagonal core with Protocol ports

## Context
Docker, pytest and one LLM vendor are today's choices, not permanent ones, and the
orchestration logic must be testable without any of them.

## Decision
`Runner` depends on three ports (`SandboxBackend`/`Sandbox`, `Agent`, `Verifier`) defined
as `typing.Protocol`s and injected through its constructor. Adapters implement the shapes
without inheriting. No module-level state, no singletons; the CLI is the only place
adapters are composed. The scorer and calibration rule are pure functions.

## Consequences
The runner is covered by fast unit tests with in-memory fakes; adapters are covered by
Docker-marked tests. Each roadmap item (Podman, other verifiers, parallel trials) is a new
adapter or a new caller of `Runner`, not a change to the core. The price is a little
indirection and a few more files than a script would need.
