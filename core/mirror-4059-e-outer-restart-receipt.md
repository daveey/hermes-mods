# Submitter records witnessed owner-restart exit

Mirror ordinals: 3984.

## Behavior

When an admitted owner cannot settle a run, the outer submitter records its own exit 76 so a bound interpreter's earlier exit 1 does not incorrectly charge the card.

## How it works

OwnerRestartLost now invokes best-effort receipt booking before returning its existing exit. The local transaction requires the task and run still running under this exact claim and this submitter PID. The full owner-restart witness includes spawn identity and every prior result/binding; an earlier interpreter receipt does not suppress it. Store failures print a diagnostic but retain exit 76.

## Code

- `hermes_cli/kanban_worker_client.py`
- `hermes_cli/kanban_worker_log.py`

## Tests

- `tests/hermes_cli/test_kanban_outer_owner_restart_receipt.py`: Real submitter main and bound interpreter subprocesses compose several inner-receipt shapes through dispatcher and observer. Assertions select the outer witnessed receipt, reclaim without failures, retain causal source identity and avoid crash/failure-finalized events. No-inner, wrong-worker/moved-claim and receipt-store-failure controls pin refusal and exit preservation.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

The shared task log is not authoritative evidence for a modern bound run. Live wedged-owner replay, racing late owner booking and network/replica boards are untested by this patch's new fixture.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
