# TUI ownership: defer update/unidentified lease owners without takeover (mirror 5720–5723, 5725)

## Behavior
- A durable ownership refusal is no longer treated as positive foreign-owner evidence; typed queue admission and automatic replay defer for update-marker and unidentified (e.g. PID-zero) lease holders without takeover.
- Namespace-qualified CLI holder telemetry is preserved.
- The queued-drain ownership precheck uses `allow_takeover=False`, avoiding a lock-order inversion with resume/redrive.
- The live provider-retry timer passes invocation-local refusal evidence so retry notes distinguish provider retries from backend crash recovery.

## How it works
`active_sessions.py`, `tui_gateway/prompt_turn.py`, `session_lifecycle.py`, `session_auto_continue.py`, `update_marker_owner.py`, `ownership_card_log.py`.

## Code
- `tui_gateway/session_lifecycle.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/update_marker_owner.py`
- `tui_gateway/ownership_card_log.py`
- `hermes_cli/active_sessions.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/tui_gateway/test_transient_lease_owner.py::test_real_submit_keeps_queue_until_holder_clears` checks typed submission/queue drain and `test_self_orphan_turn_lease.py::test_unidentified_durable_holder_defers_without_releasing_it` checks the durable fence. `test_transient_queue_lock_order.py::test_queue_drain_does_not_take_resume_under_history` checks lock order. `test_refused_retry_note.py::test_live_refusal_retry_and_crash_replay_have_distinct_notes` checks invocation-local retry wording. These are gateway/helper tests; live interactive TUI transport is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Test doubles for the helper must accept the `allow_takeover` keyword.

## plugin?
No. Core change.
