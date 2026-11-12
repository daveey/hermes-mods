# Placement forwarder: a rejected hub request is terminal

Patches: **2498**.

## Behavior

When a deferred card is forwarded to another machine through the hub and the target rejects the request (hub state `rejected`, `expired` or `failed`), the forwarder used to treat only `done` as terminal. The card was re-parked on the ten-minute `REQUEUE_SECONDS` clock forever. Now a failed request is never parked on:

- The forward names the local placeholder card in `distinct_from` (with a `distinct_reason`), so the target's overlap guard does not refuse its own copy as a duplicate of the placeholder it sees through the replica. A client that answers `create_option_unknown` gets the plain forward instead.
- A `create_overlap` rejection that names only this card is re-forwarded once under a new transport key (`...:distinct`, hence a new request id), recorded as `reforwarded`/`reforward_of`.
- An overlap with another card, or with hidden matches (`total_matches` above the shown list), blocks the card (`needs_input`) with a reason naming the matches; unblocking runs it here.
- Anything else (a second self-match, a plain rejection, a client unable to send `distinct_from`) cancels the deferral (`placement_deferred_cancelled`), so the card runs locally.

## How it works

`hermes_cli/kanban_placement.py` adds `FAILED_STATES` and `remote_rejection(rid)`, which parses the target's `kanban create --json` refusal (code, matches, total_matches, note) out of the stored hub result and never raises. `await_task_id` stops polling as soon as the request has failed. In `kanban_placement_forward.py`, `Forwarder._await` checks the state before the 45-second wait and after it and routes failures to `_rejected`; `_forward` gained `reforward_of` and a `_create` helper. See [kanban-placement-cancellation.md](kanban-placement-cancellation.md) and [kanban-worker-first-placement.md](kanban-worker-first-placement.md).

## Code

`hermes_cli/kanban_placement.py`, `hermes_cli/kanban_placement_forward.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_placement_rejected.py`. Entry path: `Forwarder.run()` against the placement test module's fake hub and board, with cards made through the `kanban_create` tool handler; plus `remote_rejection` on a stored row. Covers rejection on wake and during the wait for self-overlap, other-overlap and plain shapes (never a `scheduled` event), one re-forward with `distinct_from` under a new id, no third forward, hidden matches, and the old-client fallback. A real hub and the dispatcher/CLI entry that invokes the forwarder are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The forward now sends `distinct_from`/`distinct_reason` when the hub client advertises create options; targets must accept them or the plain forward is used.
- Requests already rejected before this change are handled on their next wake by the new path.

## Plugin

Core change; no plugin required.
