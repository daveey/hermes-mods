# Continued run's clean exit is a protocol violation

Patches: **3698**.

## Behavior
- A Kanban run that was checkpointed and continued by a new owner (for example after a gateway restart), whose worker then exits with code 0 without calling complete/block/schedule, no longer stays `running` forever. One dispatcher dead-worker sweep returns the card to `ready` and books the run as a `protocol_violation`, quoting the worker's last output, as for a plain run.
- A continued run whose worker fails (nonzero exit) is still a crash. A continued run whose interpreter is still alive with a clean result is still held.
- `kanban_comment` (tool) and `hermes kanban comment` (CLI) stop promising "a live worker run" once the run's worker has exited: the note is said to wait for the card's next run, and the tool returns `live_run: false, worker_exited: true`. A live worker, or a checkpointed turn with a live submitter, keeps the live-run promise.

## How it works
- 3698 makes `_record_worker_result` write the death-proof identity fields for a clean exit too, when the run already holds modern (continued-turn) evidence. Plain runs are unchanged. The failed-result observer is still armed only for nonzero codes.
- `failed_result_receipt` gains `allow_clean`; the dispatcher sweep (`reclaim_proof`) accepts a modern exit 0, provided every recorded process of the run is gone.
- New `kanban_comment_liveness.py` decides whether a run is live: not live when its final worker result is booked after its last binding and admission, or every pid recorded on this host is dead.

## Code
- `gateway/session_kanban.py`
- `hermes_cli/kanban_failed_result.py`
- `hermes_cli/kanban_comment_liveness.py`
- `hermes_cli/kanban.py`
- `tools/kanban_tools.py`
- `tests/hermes_cli/test_kanban_comment_liveness.py`
- `tests/hermes_cli/test_kanban_owner_restart_failed_receipt.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_comment_liveness.py`: `test_the_cli_comment_on_an_exited_worker_says_the_note_waits`, `test_the_comment_tool_on_an_exited_worker_claims_no_live_run`, `test_a_live_worker_keeps_the_live_run_promise`, `test_a_checkpointed_turn_with_a_live_submitter_is_still_live`.
- `tests/hermes_cli/test_kanban_owner_restart_failed_receipt.py`: `test_a_continued_run_whose_worker_exits_cleanly_is_a_protocol_violation`, `test_a_continued_run_whose_worker_fails_is_still_a_crash`, `test_a_live_continued_interpreter_with_a_clean_result_still_holds`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only a continued (modern-evidence) run gets the clean-exit marking; plain runs keep the legacy rc=0 path.
- The sweep still requires every recorded process of the run to be gone before reclaiming.
