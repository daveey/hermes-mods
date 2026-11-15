# Causal loss receipts after worker rebind

Mirror ordinals: 4004.

## Behavior

A modern continued worker lost before its next result can be reconciled as one crash instead of retaining a claim indefinitely; unknown exit status remains unknown.

## How it works

The owner's loss transaction adds loss_receipt and result-identity fields only for a bound positive PID with modern history, valid recorded process witnesses and the latest matching claim binding. Marked losses join result/binding causal history and sequence allocation; legacy diagnostic losses do not. Recovery accepts a witnessed bound loss with exit_code=None and retains ordinary process-death and supersession fences. Reconciliation carries the bounded loss reason into the crash diagnostic.

## Code

- `gateway/session_kanban.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_failed_result.py`
- `hermes_cli/kanban_failed_result_identity.py`
- `hermes_cli/kanban_lost_result.py`

## Tests

- `tests/hermes_cli/test_kanban_rebound_worker_loss.py`: Restart/rebind/loss scenarios through dispatcher and observer end the run once as crashed, requeue with exactly one failure and retain EOFError. Live-process, wrong-PID, wrong-claim, unbound, later-binding and later-result controls hold the task running.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Do not infer an exit code from EOF/loss. A loss is not authority to close an unproven or superseded attempt, and older unmarked loss events retain legacy treatment.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
