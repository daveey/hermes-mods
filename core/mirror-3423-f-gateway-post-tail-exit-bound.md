# Gateway post-tail exit bound: exit code, diagnostics, hand-back

Patches: **3259, 3292**.

## Behavior
- When the gateway's shutdown tail raised (or `SystemExit`), the post-tail exit bound arms with the exit code the unwinding exception will produce, not the tail's own verdict.
- A bound exit writes a `gateway.post_tail_bound` exit-diag record (exit code, seconds over the target, target reason, phase marks) before taking the funnel.
- If the bound's funnel fails, including with a `BaseException` such as `SystemExit`, it hands the exit back and a main thread parked in the funnel wakes immediately instead of waiting for the hard deadline; non-`Exception` errors are re-raised.
- The `except SystemExit` branch reuses `_unwind_exit_code` rather than a duplicate mapping.

## How it works
- 3259 reads the active exception in the tail, adds the exit-diag record in `gateway_exit_diag`, and parks the funnel on a module `_handback` event that the watcher sets on failure, then retries the disarm. 3292 widens the watcher's hand-back to any `BaseException`, re-raising non-`Exception` ones.

## Code
- `gateway/exit_deadline.py`
- `gateway/run_bootstrap.py`
- `hermes_cli/gateway_exit_diag.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_exit_deadline.py`: `test_post_tail_bound_exit_leaves_an_exit_diag_record`.
- `tests/gateway/test_update_exit_target.py`: `test_post_tail_bound_takes_the_unwinding_exception_code`, `test_post_tail_bound_writes_an_exit_diag_record_before_the_funnel`, `test_exit_funnel_wakes_when_the_bound_hands_the_exit_back` (adjusted by 3292 to parametrize over failure types).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- None recorded.
