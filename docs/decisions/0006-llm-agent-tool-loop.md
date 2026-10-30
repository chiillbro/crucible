# 0006. A minimal, explicit LLM tool loop; no silent model fallback

## Context
The harness needs a real agent to demonstrate end-to-end evaluation, but the agent is not
the product; the measurement is. Readers must be able to audit every step a model took.

## Decision
`LlmAgent` is a plain loop over the Claude Messages API with three custom tools (`bash`,
`write_file`, `str_replace`), strict JSON schemas, an append-only transcript, a step cap,
and per-call timeouts clamped to the trial deadline. Invalid tool calls are returned to the
model as `is_error` results; API failures are `AgentError`; `refusal`, `max_tokens` and
`max_steps` are recorded as stop reasons on the trial.

The API's server-side refusal fallback (re-running a declined request on another model) is
deliberately **not** enabled. The model under evaluation is recorded in results; silently
substituting a different model would invalidate the measurement. A refusal is a data point.

Thinking is left at the model's default and `effort` is not set, so a run measures the
model as configured by its id alone. Sampling parameters are not sent: current models
reject them.

## Consequences
Less capable than a full agent framework (no planning scaffolds, no retrieval), but every
behaviour is a few lines of readable code and the dependency surface is the SDK only. The
model id, token usage and stop reason are recorded per trial. A richer agent is a new
adapter behind the same `Agent` port.
