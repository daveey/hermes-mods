# Pinned-machine core-update drain notes

Patches: **2462, 2463**.

## Behavior

A card pinned to another machine whose dispatcher is draining for a core update used to be reported as "it runs on <machine> ... do not re-create it" and "queued, not stuck", so a chat could archive it as lost while it was simply waiting out the drain. Now, when every machine that can run the card reports a fresh drain, the `kanban_create` note, the `kanban_show` `queue_note` text and the chat-wait notice say the target is DRAINING for a core update, give the announced end bound (marked "not guaranteed") or "end time unknown", and say the card starts when the drain ends and should be re-placed only if it cannot wait. If only some candidate machines drain, the plain wait line gains a "(<machines> draining for a core update)" suffix. A drain sample older than 15 minutes is ignored. Patch 2463 rewords the headline to "its dispatcher claims no new cards until the drain ends" (dropping the claim that it "imports" nothing) and moves the create note into a helper.

## How it works

`kanban_queue_position.draining(entry)` reads `drain_active` / `drain_until_ms` from a machine's Board registry `metrics` (valid while `metrics["at"]` is within `DRAIN_SAMPLE_MAX_AGE_S`, 15 minutes). `placement()` adds `draining` and `drain_until_ms` to a `machine`-lane place; `headline()` and `describe()` render the wait, sharing the text with the chat-wait notice. `kanban_parent_unclaimed._queue_words` now uses `kanban_queue_position.headline` for `machine` and `unplaceable` lanes instead of "queue place unknown". In `tools/kanban_tools_placement.py`, `_pin_drain_note` (never raises) appends the drain sentence and `_pinned_note` (patch 2463) builds the full create note used by `_handle_create`. Related: [core-update-drain-hold.md](core-update-drain-hold.md), [kanban-queue-reasons-repin.md](kanban-queue-reasons-repin.md).

## Code

`hermes_cli/kanban_queue_position.py`, `hermes_cli/kanban_parent_unclaimed.py`, `tools/kanban_tools_placement.py`, `tools/kanban_tools.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_draining_machine_pin.py`. It covers the `kanban_create` tool note (through the tool handler), `kanban_queue_position.placement`/`describe`, the chat-wait `_queue_words` text, stale and non-draining samples, and a two-gateway claim once the hub is not draining. The `hermes kanban show` CLI rendering and the exact reworded headline of patch 2463 are untested (the test asserts only `DRAINING`, `end time unknown` and `starts when the drain ends`).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The drain state is only as fresh as the machine's last metrics sample; a machine that stopped reporting shows the plain wait. The announced bound is a hint, not a guarantee. The notes are informational: nothing is re-placed automatically. The commit message traces the motivating case to a drain window, not to the card body's local marker.

## Plugin

Core change; no plugin required.
