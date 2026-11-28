# Origin-forward refusals, queued-create wait handles and wait-check parsing

Patch coverage: 6644, 6645, 6657, 6664, 6665, 6756, 6757, 6787, 6788, 6789, 6790, 6791, 6792, 6793, 6813, 6814, 6815, 6816, 6835, 6930, 6931.

## Behavior

When a worker creates a card that is forwarded to its origin gateway, the forward can be refused or may never produce a task id. This series makes those outcomes terminal and visible instead of pending forever.

- A forwarded create whose assignee lacks the needed capabilities on every candidate machine is answered with a typed terminal *rejection* (it raised a placement-capability error before any write and used to answer "pending" indefinitely). Untyped failures and waiting create-authority refusals stay pending. The bare self-work courier path gets the same typed rejection.
- Definitive pre-queue refusals and courier option-validation refusals settle the forward obligation instead of leaving it open.
- Queued creates return a *wait handle*: an argv-quoted `wait-check key <KEY>` for keyed requests, or bounded lookup guidance for keyless ones, on both the legacy and canonical-home queued result paths. Stale key shadows are rejected on every read path.
- A creator may not park on a child key whose creation was definitively rejected (before any owner create). Every key in a `&&` conjunction and other creators' requests are judged; alternatives with `||` are not refused wholesale.
- The wait-check lexer reads operators however spaced (`A|| L`, `L||true`), treats only unescaped line continuations as continuations, accepts non-ASCII keys and `>|` redirects, and keeps only the last command of `;`/newline sequences (it decides the exit status).
- Standing-owner guidance: a worker whose only owner chat is the standing owner on its own gateway now gets an explicit reason why it cannot file a canonical-home child.
- The creator lease is retained until origin forwards settle; forward obligations are reserved before courier enqueue; courier reads use a guarded read-only connection. Origin parents are reconciled, and a create with a missing *linkable* parent is refused (falsy parent entries are ignored, matching `create_task`).

## How it works

`hermes_cli/kanban_origin_forward_wait.py` (`wait_key`, `wait_keys`, `rejected_wait`, `_lost`, `_words`) implements the lexer and gate; `kanban_wait_key.py` maps keys to sources; `kanban_creation_wait.py` builds `queued_wait` handles; `kanban_origin_forward_run_end.py` (`reserve`, `require_settled`, `settle_unsubmitted`) holds the lease/obligation; `kanban_origin_creation.py` types `OriginCreateRefused`; `kanban_origin_owner.py`, `kanban_placement.py` and `tools/kanban_tools_placement.py` (`_standing_owner_refusal`) carry the typed results and guidance.

## Code

- `hermes_cli/kanban_origin_forward_wait.py`, `kanban_origin_forward_run_end.py`, `kanban_wait_key.py`, `kanban_wait_check.py`, `kanban_creation_wait.py`, `kanban_creation_home.py`, `kanban_origin_creation.py`, `kanban_origin_owner.py`, `kanban_placement.py`, `kanban_timed_park.py`, `kanban_awaiting.py`
- `tools/kanban_tools_placement.py`, `tools/kanban_tools.py`

## Tests

Read from source diffs; **not executed here**.

- `test_origin_forward_rejected_wait.py`, `test_origin_forward_run_end.py`, `test_origin_forward_delayed_park.py`, `test_kanban_wait_key.py`, `test_kanban_origin_owner.py`, `test_kanban_selfwork_nochat.py`, `test_origin_create_validation.py`, `test_kanban_awaited_card_wake.py`, `test_kanban_board_replica_refs.py`, `test_standing_owner_retry.py` (`tests/hermes_cli/`), and under `tests/tools/`: `test_standing_owner_placement.py`, `test_kanban_same_gateway_standing_owner.py` (the `kanban_create` tool refusal `origin_same_gateway_standing_owner`) and `test_kanban_worker_review_origin.py`.
- Entry paths: the forwarded-create and bare self-work courier paths have named tests. The tool surface for standing-owner refusal is covered only as far as the placement tests go; other create entry points are **untested** in this mirror.

## Applying

Apply the listed ordinals in order with the series; later commits fix earlier ones (e.g. 6813 → 6814 → 6815/6816 → 6835).

## Gotchas

- A refused probe never blocks a park that was already allowed before the gate; the lexer corrections only widen what is *recognised*.
- Typed rejection must not swallow waiting authority refusals (they remain pending).

## Why core, not a plugin?

It is the Kanban create/park path inside core.
