# Placed create: keep the idempotency key, report a rejection

Patches: **2641, 2642**.

## Behavior

- A placed (origin-courier) `kanban_create`, where the target gateway creates the card, now stores the creator's `idempotency_key` on the new card. Before, it was dropped to NULL, so probes and guards that look a card up by its requested key never matched. (2641)
- If a live (non-archived) card on the target board already holds that key, the create is refused with typed code `origin_idempotency_conflict` naming that card. It is never an adoption and never a duplicate. The owner adapter maps it to a terminal `rejected` request. An exact retry of the same request is still answered by the request id. Transport keys on plain creations are unchanged. (2641)
- When the target rejected, expired or failed a placed create, the tool returns an error with `created: false` and the target's code, instead of telling the creator "The card IS created ... Do NOT call kanban_create again" with a null task id. For `origin_idempotency_conflict` the result carries `existing_task_id` and says to read that card or pick another key; any other refusal says no card was created and why. A still-pending request (or `queued_only`) keeps the "IS created" note. (2642)

## How it works

- 2641: `create_with_origin` (`kanban_origin_creation.py`) checks the key inside its write transaction (`SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived'`) and raises `OriginError("origin_idempotency_conflict", ...)`; otherwise it keeps the key in `options` for `create_task`. `kanban_origin_owner._dispatch_result` adds the code to the set that returns `status: rejected`.
- 2642: new `_placed_rejection` in `tools/kanban_tools.py`, called from `_place_remote` after the request is awaited; it reads `remote_rejection(request_id)`. `existing_task_id` is parsed from the refusal text.

## Code

`hermes_cli/kanban_origin_creation.py`, `hermes_cli/kanban_origin_owner.py`, `tools/kanban_tools.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_origin_creation.py`, `test_kanban_origin_create_tool.py` (2641), `test_kanban_placement_rejected.py` (2642). Entry path: `create_with_origin` and the `kanban_create` tool's placed-create path against a faked target request state. A real second gateway and the Board transport are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The key is matched on the target's own board only; an archived card holding the key does not conflict.
- `existing_task_id` is recovered from the refusal text with a regex; a reworded refusal drops it, leaving the generic error.
- The key now persists on placed cards, so a later create with the same key on that board is refused where it used to succeed.

## Plugin

Core change; no plugin required.
