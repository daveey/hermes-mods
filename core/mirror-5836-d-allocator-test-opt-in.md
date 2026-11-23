# Explicit bounded allocator opt-in for the canonical test runner

Source patch ordinals: 5746.

## Behavior

The isolated test runner forwards an explicitly supplied `MALLOC_ARENA_MAX`. It imposes no allocator default and does not broaden general environment passthrough or change resource caps.

## How it works

Before launching Python, setup or test children, `scripts/run_tests.sh` accepts only canonical decimal integers from 1 through 1024. A validated assignment is added to the final `env -i` invocation. The offline harness exercises the real activation/re-exec chain and records the pytest child's environment and resource observations.

## Code

- `scripts/run_tests.sh`
- `tests/pm/run_tests_harness_support.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/scripts/test_run_tests_allocator_env.py` covers explicit interpreter selection, current activation, missing/stale activation with one setup re-exec, stale refresh refusal and invalid inputs rejected before children. The harness checks selected synthetic environment sentinels and Linux resource receipts.

## Applying

Apply the runner change with its harness. Set `MALLOC_ARENA_MAX` explicitly only for the test invocation that requires the opt-in; leaving it unset preserves prior behavior.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

The provisioner is offline fixture infrastructure, not locked-install proof. The source tests do not establish comprehensive intermediate-process environment isolation or a runtime memory remedy.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
