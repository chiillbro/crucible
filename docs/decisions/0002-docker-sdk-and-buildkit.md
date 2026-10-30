# 0002. Docker SDK for containers, BuildKit CLI for image builds

## Context
Containers need programmatic control: create with limits, exec with captured streams,
archive in and out, kill, remove. The `docker` Python SDK does this with typed-ish objects
and clean error classes. Its image *build* path, however, uses the legacy builder, which
Docker Engine 29 marks "deprecated and will be removed in a future release", and which
cannot build modern Dockerfiles (heredocs, cache mounts).

## Decision
`DockerSandboxBackend` uses the SDK for everything except builds, which shell out to
`docker build` with `DOCKER_BUILDKIT=1` and a subprocess timeout. The split is internal to
the adapter; the `SandboxBackend` port does not expose it.

## Consequences
Builds get BuildKit's cache and Dockerfile support and keep working when the legacy builder
is removed. The CLI must be on `PATH` and point at the same daemon as the SDK, which holds
for every standard install; a missing CLI is reported as a `BuildError`. Podman or remote
backends become new adapters behind the same port.
