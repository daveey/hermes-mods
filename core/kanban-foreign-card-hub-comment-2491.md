# foreign_card refusal names the hub-comment route

Patches: **2483, 2484**.

## Behavior

A worker's `kanban_comment` on a card owned by the hub is refused as `foreign_card` because this gateway holds only a replica. The refusal message used to name no route, so workers filed relay cards just to post one comment. When the owner is `hub` or `hermes-hub` and a `hub-comment` helper is available on the machine, the message now appends: to add a comment only, run `hub-comment <task_id> --text '...'` (or `--file <path>`) from the card's terminal; it posts on the hub attributed to the calling card, prints the comment id, and no relay card should be filed. Completion, status, links and archive stay with the owner. For other owners, or without the helper, the message is unchanged.

## How it works

`hermes_cli/kanban_foreign_card.py`: `message()` appends `comment_route(task_id, owner)`. `comment_route` checks the lowercased owner against `HUB_OWNERS` and finds the `hub-comment` command. 2483 used `shutil.which`; 2484 resolves it with `hermes_platform.resolver.locate_command(HUB_COMMENT).command` so the managed-runtime allowlist guard accepts it. The helper itself lives outside core. Existing behavior: [kanban-foreign-card-replica.md](kanban-foreign-card-replica.md).

## Code

`hermes_cli/kanban_foreign_card.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_foreign_card_writes.py` (tool comment on a hub card names the route; route only for hub cards with the helper present). The 2484 switch to `locate_command` has no separate test; the existing tests were not changed by it, so whether they stub `shutil.which` or `locate_command` is untested here. CLI and REST write paths that reuse `message()` are untested by these patches.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The hint is advisory text only; nothing in core calls the helper or checks that the worker has the capability to write to the hub.
- Detection depends on the helper being resolvable on the calling machine, so the same refusal can read differently on different hosts.

## Plugin

Core change; no plugin required.
