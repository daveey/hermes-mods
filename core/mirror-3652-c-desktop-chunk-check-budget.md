# Desktop chunk-check budget: integer-only timeout

Patches: **3652**.

## Behavior
- `HERMES_CHUNK_CHECK_TIMEOUT_MS` must be an integer; a fractional value (e.g. 1500.5) falls back to the 30-minute default instead of throwing `RangeError` from `checkDistBuilt`.
- The harness hint now also names a timeout on a throttled or contended host and the override variable, not only Windows process-creation stalls.

## How it works
- 3652 requires `Number.isInteger` in `chunkCheckTimeoutMs` in `assert-dist-built.mjs`, updates the hint in `main()`, and comments why the override is an environment variable (a build-script knob read before any config loads).

## Code
- `apps/desktop/scripts/assert-dist-built.mjs`
- `tests-js/renderer-syntax-timeout.test.mjs`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests-js/renderer-syntax-timeout.test.mjs`: `a non-integer budget falls back to the default instead of crashing spawnSync`, `a fractional budget in the environment still lets checkDistBuilt return a verdict`.
- The hint text change is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- None recorded.
