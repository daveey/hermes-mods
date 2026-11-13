# Test hardening: sandbox exit probe and requeue single-write

Patches: **3643–3644**.

## Behavior
- The sandbox normal-exit CI probe no longer crashes when an unrelated host process is reaped while it scans process namespaces.
- A new test pins that `requeue_unstarted_input` re-queues the wake and hands back the steered prompt in one atomic write; if the steer hand-back fails, the wake re-queue is rolled back too.
- Test-only: no runtime behavior changes.

## How it works
- 3643 treats `ESRCH` (ProcessLookupError) from reading `/proc/*/ns/pid` like `ENOENT`: a reaped task is outside every namespace.
- 3644 adds a test where the transfer step raises for the steer; the wake must stay `started` at its generation and the steer stay `steering`. It kills a mutant that split the work into two writes.

## Code
- `tests/ci/test_plugin_sandbox_normal_exit.py`
- `tests/hermes_state/test_requeue_unstarted_input.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_plugin_sandbox_normal_exit.py`: probe fix only; no new test names (the existing probe test is changed).
- `tests/hermes_state/test_requeue_unstarted_input.py`: `test_a_failed_steer_hand_back_rolls_the_wake_requeue_back_too`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The 3643 repro needs many forking processes and a multi-CPU host to show within seconds; it is a race, not deterministic.
