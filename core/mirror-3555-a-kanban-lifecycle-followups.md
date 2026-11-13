# Kanban follow-ups: review-reopen pin, capability wake text, parent-missing clear

Patches: **3536–3538**.

## Behavior
- A `review_reopened` event after a completion is treated as a reopen: a parked (scheduled/blocked) card is not held as terminal and unblocks to ready (3536, tests only).
- Capability-owner rejection wake text now promises a fresh generation-2 request only where routing grants one; otherwise it says the route is exhausted and what to do instead, with cleaner "expired before"/"failed on" wording (3537).
- When every parent link is removed while the parent-missing detector's block holds, the unblock records `source=parents_unlinked` (not `parent_found`), and the repair-card note says which case cleared it (3538).
- The repair note is a system comment, not attributed to the repair worker's own run, so the live comment bridge no longer skips it for that worker (3538).

## How it works
3536 adds tests pinning `review_reopened` in the terminal guard's reopen kinds and the review-reopen path. 3537 makes `_rejected` in `kanban_capability_routing.py` read the generation from the request id (`::g<N>`) and state, and updates `kanban_overlap.py` docs and comments. 3538 gives `add_comment` (`kanban_db_comment_ids.py`) a `system=True` option with no writer provenance, used by `_tell_repair_card`, and changes the unblock source and note in `kanban_db_graph.py`.

## Code
- `hermes_cli/kanban_capability_routing.py`
- `hermes_cli/kanban_overlap.py`
- `hermes_cli/kanban_db_comment_ids.py`
- `hermes_cli/kanban_db_graph.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_capability_owner_origin_mirror.py`
- `tests/hermes_cli/test_kanban_parent_missing_self_clear.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_a_review_reopen_after_the_completion_is_a_reopen`, `test_a_review_reopen_after_a_completion_and_reopen_cycle_lands_and_is_not_held`.
- `tests/hermes_cli/test_kanban_capability_owner_origin_mirror.py`: `test_wake_text_promises_a_fresh_request_only_when_routing_grants_one`.
- `tests/hermes_cli/test_kanban_parent_missing_self_clear.py`: `test_every_parent_unlinked_returns_the_detector_blocked_child_to_ready`, `test_clearing_the_block_comments_on_the_open_repair_card`, `test_the_repair_note_is_not_the_repair_workers_own_comment`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Overlap-exemption matching compares awaiting refs by bare task id (relies on globally unique ids) and trusts any body line shaped like a forward footer; both are heuristics noted in comments only (untested).
