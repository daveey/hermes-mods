# Withhold rejected final output until final transforms, and a surviving submit

Patches **0500** (source `e73677e9fc`, F3 core companion, PR #105) and **0479** (`3c3effe767`).

## Behavior

- 0500: a final reply rejected by a pre-final gate is not delivered across any delivery boundary: buffered candidates
  stay private through output transforms and are released once, after final transforms. Replacement results override
  hold/append in either order, conflicting replacements deliver neither candidate, a replacement cannot erase an
  unresolved rejection, and the budget is total (passed only to opted-in callbacks). Hooks docs advertise the buffered
  final-publication contract.
- 0479: an accepted `prompt.submit` survives a client socket drop. An unanswered user row is flagged "not sent" on
  reattach (not for answered/running tails or sessions another process is answering), and build failures or pre-agent
  exits of the deferred agent build are now logged instead of silent.

## How it works

`agent/pre_final_gate.py`, `final_gate_fallback.py`, `turn_final_response.py`, `turn_finalizer.py`, `turn_stop_gates.py`
buffer and release the candidate. For 0479, `tui_gateway/methods_prompt.py`, `session_lifecycle.py`,
`session_history.py` and `hermes_state_messages.py` keep the submit alive and flag unanswered rows.

## Code

Files above plus `website/docs/user-guide/features/hooks.md` and `tui_gateway/{server,methods_session}.py`.

## Tests

`tests/agent/test_pre_final_gate*.py`, `test_final_gate_*.py`, `test_final_transform_buffer.py` (agent path);
`tests/tui_gateway/test_submit_survives_reconnect.py` (edit submit survives socket drop past reap grace, failure
logging, reattach flags, skip foreign-answered sessions). REST and CLI entry paths for 0479 are untested.

## Applying

`git am -3` in number order; builds on [pre-final-gate.md](pre-final-gate.md).

## Gotchas

`hold=True` is bounded by the attempt budget; do not loop the gate without it.

## Plugin?

No: both live in the turn finalizer and TUI gateway.
