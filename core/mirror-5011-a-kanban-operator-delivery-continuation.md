# Kanban operator delivery receipts across continuation runs

Mirror snapshot 5011; ordinals 4399, 4400.

Entries:

- **4399**: fix(kanban): retain operator delivery across continuation runs
- **4400**: fix(kanban): fence continued operator receipts to current claim

## Behavior

- Delivery belongs to the original authenticated requesting task and run, not only the current attempt: historical same-task run UIDs are matched, and on moved continuations without the gateway-local ledger the original event is located in the replica and the exact request and applied result are authenticated with canonical Board evidence.
- Completion belongs to the current admitted claim: the current run and complete request are frozen for the transactional recheck. Carried events locate evidence but never authorize it; local pending/failed refusal stays, and proofs are required for carried operator history even when the target exists locally after migration. No request rewriting, duplicate posts or DB repairs.

## How it works

`kanban_operator_delivery.py` plus new `_moved.py` and `_custody.py` siblings and `kanban_completion_receipt.py`; two earlier receipt-continuation implementations are consolidated.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_completion_receipt.py`
- `hermes_cli/kanban_operator_delivery.py`
- `hermes_cli/kanban_operator_delivery_custody.py`
- `hermes_cli/kanban_operator_delivery_moved.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_operator_delivery_continuation.py`
- `tests/hermes_cli/test_kanban_operator_delivery_prior_run.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- A missing ledger alone is recoverable on the same host; a missing original request is not.
- Captured-row replays described in the commit are not live canonical authentication.
