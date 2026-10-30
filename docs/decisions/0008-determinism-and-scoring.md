# 0008. Determinism and the pass@k estimator

## Context
Calibration only means something if the oracle and verifier are deterministic, and pass@k
only means something if it is computed the standard way and never fabricated.

## Decision
The oracle copies a fixed file and runs a fixed script. The verifier runs pytest with the
cache provider disabled, no network, and no dependence on the clock. pass@k is the unbiased
estimator from Chen et al. (2021), `1 - C(n-c, k) / C(n, k)`, computed with `math.comb` and
`fractions.Fraction` and converted to float once. When the statistic is undefined (no
trials, or `k > n`) it is reported as `null` and the CLI says so; it is never clamped or
guessed. Trial rewards are strictly 0 or 1.

## Consequences
Results are comparable with the literature. LLM sampling is not seeded because the Messages
API does not expose a seed; the model id, token usage and stop reason are recorded per trial
instead, and variance is what the N trials measure.
