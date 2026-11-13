# Desktop renderer chunk check: budget sized for a throttled background build

Patches: **3613**.

## Behavior
- The renderer chunk syntax check in the Desktop dist assertion now has a default budget of 30 minutes (`DEFAULT_CHUNK_CHECK_TIMEOUT_MS`) instead of a fixed 300 s. An unattended background build that runs at low CPU/I/O priority no longer fails a bundle that built cleanly just because the check was slow.
- `HERMES_CHUNK_CHECK_TIMEOUT_MS` overrides the budget; empty, zero, negative or non-numeric values fall back to the default.
- A real overrun is still a `harness` failure (never `bundle`) and its message names the budget it hit and the environment variable.

## How it works
- 3613 adds `chunkCheckTimeoutMs(env)` to `apps/desktop/scripts/assert-dist-built.mjs` and uses it as the `spawnSync` timeout in `verifyChunksParse`. On `ETIMEDOUT` the error text gains "did not finish within N s; HERMES_CHUNK_CHECK_TIMEOUT_MS sets the budget".

## Code
- `apps/desktop/scripts/assert-dist-built.mjs`
- `tests-js/renderer-syntax-timeout.test.mjs`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests-js/renderer-syntax-timeout.test.mjs`: `defaults to a budget sized for a throttled background build, not an idle box`; `a slow node passes within the budget; only overrunning it is a harness failure` (skipped on Windows; uses a wrapper node that starts 1.5 s late, passes with a 30 s budget, and fails as `harness` with a 300 ms budget).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A genuinely stalled check now costs up to 30 minutes before it fails.
- The slow-node test is skipped on Windows.
