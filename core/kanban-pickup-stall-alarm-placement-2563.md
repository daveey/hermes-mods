# Pickup-stall alarms never pin to the stalled machine

Patches: **2506**.

## Behavior

The pickup-stall detector files an agent repair card (an "alarm") when a card pinned to machine M sits ready and is never imported. Every alarm used to be pinned `runs_on=[machine:M]`, but M is exactly the machine taking no Board work (a registry hold, or no spawn slot: `budget_exhausted`), so the alarm waited out the same condition it reported, and could even take M's last slot ahead of the card it reported. Now:

- The alarm is never pinned to M. It goes, in order, to: another active, unheld, pickup-advertising machine eligible now (carries `capability:terminal/file/kanban` and `profile:<assignee>`, has hub access, is not draining, seen within 300 s, has a free slot; most free slots wins); the hub operator lane; any other host regardless of capacity; another held machine (hub first); otherwise default placement. With an unreadable registry it goes to the hub lane, or, when M is the hub, to a label set the hub does not carry.
- An open, unclaimed covering alarm whose pin is stranded (names M, or a machine that can no longer host it) is re-pinned once with a detector comment. Running or claimed alarms and healthy pins are untouched.
- The body carries M's machine-local evidence (pickup skip per listed target, M's registry entry, M's replica path) and no longer says "on this host".
- Detector thresholds, grouping and hold handling are unchanged; nothing lifts or shortens a hold.

## How it works

`hermes_cli/kanban_pickup_stall.py` adds `alarm_placement(own, machines, assignee=...)` (using `_can_host`, `_ready_now`, `_free_slots`), `_stranded`, `_replace_stranded`, `_registry_line` and `_tail`. `_create` reads the registry, computes the placement, passes `runs_on` to `kb.create_task`, and calls `_replace_stranded` when reusing an existing alarm. Related: [kanban-pickup-evidence-grace-2409a.md](kanban-pickup-evidence-grace-2409a.md), [kanban-pinned-pickup-repair-2253.md](kanban-pinned-pickup-repair-2253.md), [kanban-worker-first-placement.md](kanban-worker-first-placement.md).

## Code

`hermes_cli/kanban_pickup_stall.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pickup_alarm_placement.py`. Entry path: the detector's alarm filing and re-pin in a fake-Board two-gateway harness (hold and `budget_exhausted` alarms, hub pickup claiming the alarm while M is held, legacy alarm re-pinned once, no churn of healthy pins, running/claimed never touched), and `alarm_placement` / `_stranded` called directly over registry fixtures (eligibility, free slots, hub lane, held fallback, unreadable registry). The real dispatcher tick and CLI/Desktop surfaces are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- With no other machine that copies cards, placement falls back to default, so a single-machine setup is unchanged in effect.
- Re-pinning adds a detector comment on legacy alarms the first time they are swept.

## Plugin

Core change; no plugin required.
