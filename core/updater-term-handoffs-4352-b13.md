# Authenticated updater TERM receipts preserve review handoffs

Patches: **4324–4325** (4352 snapshot).

## Behavior

A same-run authenticated successful updater TERM receipt, paired with its exact crash diagnostic, preserves an orderly review continuation. Unmatched or genuine independent crashes, timeouts and other failures still consume the handoff.

Foreign-parent and ordering review-evidence consumers skip only requeues accepted by the shared recognizer.

## How it works

The recognizer requires the updater reclaim evidence already accepted by the core-update requeue path, then correlates the diagnostic to the same run, positive integer PID, nonblank matching claim, exact end/event second and signaled exit code 15. The receipt must affirm host-local termination attempted and succeeded, with sigkill explicitly false; worker-lost and attempt-note diagnostics do not qualify. History is read, not rewritten.

The two source commits are e2764f4009d49f82c71c6c36eae559955bdfc1a9 and a5c69b24f6ca3021cc62e18cfd93a6016b652161. Consumer tests retain the handoff for matching TERM and consume it for a real successor fault. The follow-up isolates malformed PID and claim cases.

## Code

- `hermes_cli/kanban_core_update_requeue.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_move_parent_evidence.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_updater_term_handoff.py`
- `tests/hermes_cli/test_kanban_updater_term_receipt.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Do not generalize this to every SIGTERM or updater-related crash. Missing, conflicting, malformed, wrong-run, wrong-time or wrong-signal evidence fails closed. Reported prior implementation evidence: 57 focused tests passed, 21 mutants killed, and independent runs of 144 and 58 tests passed. These are supplied prior results, not executions by this mirror documentation pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
