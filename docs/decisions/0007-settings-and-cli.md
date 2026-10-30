# 0007. pydantic-settings for configuration, Typer for the CLI

## Context
Secrets (the API key) and operator defaults (model, log level) must never live in code or
in task files. The CLI is the primary interface and should be self-documenting.

## Decision
`Settings` is a `pydantic-settings` model reading `CRUCIBLE_*` variables and the
conventional `ANTHROPIC_API_KEY` from the environment or a git-ignored `.env`;
`.env.example` documents every key. Task-specific limits stay in `task.toml`, not in
settings. The CLI is a Typer app with two commands, `run` and `validate`, typed options with
validation (`--trials` and `--k` must be at least 1), and distinct exit codes: 0 success,
1 run or calibration failure, 2 usage error.

## Consequences
Configuration is typed and validated at startup with clear errors. Typer brings Rich as a
transitive dependency, which is accepted for `--help` quality; summaries are plain text so
output pipes cleanly.
