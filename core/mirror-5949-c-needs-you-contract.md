# One shared "needs you" attention predicate

Patch: **5947**. Adds `hermes_cli.kanban_open_asks.needs_you(card)` and uses it for the lanes reason classifier.

## Behavior

One boolean decides whether a card currently needs the user, for lane classification and card presentation alike. It is true for a complete open human request on a blocked/triage card, a projected human wait, and a bounded owner-device step on a scheduled card. It is false for running/done cards, stale cards, incomplete requests (missing required fields), and a blocked card with only a `block_kind`.

A stale historical `block_kind` can no longer make a scheduled no-request card red: a card moved from blocked to scheduled with its request cleared is not `needs_you` and classifies as a time wait. The legacy scheduled device probe is retained but bounded: it counts only when its `wake_check` is an owner-device step; a passive capture check does not. This is presentation only; it grants no consent, revives no retired request, authorizes nothing and mutates nothing.

Lane snapshot cards with reason `you` now also carry an `attention` row (id, title, status, human request, block kind and a wait description) so a sidebar that has no linked session can still show the ask.

## How it works

`needs_you` accepts a dict with `status`, `block_kind`, `human_request` (object or stored JSON), optional `wait`, `wakes`, `stale`, `park` and ordered `events`. Order: status/stale gate; an explicit `wait.wait_state` wins; then the existing open-ask check; for scheduled cards a `wakes.blocks_for_human` plus action; finally the latest `scheduled`/`continued` park payload (stopping at claimed/unblocked/completed/archived) is checked with `kanban_wait_info.owner_device_step`. `classify_reason` calls it in place of the old inline `human_request or block_kind` test. `snapshot` builds `attention` from the last park event.

`hermes_cli/data/plugin_api.json` and the public-API/Kanban validation docs publish `needs_you` by name only; other module names stay private.

## Code

- `hermes_cli/kanban_open_asks.py` (`needs_you`), `hermes_cli/kanban_lanes_flow.py` (classifier, `attention`).
- `hermes_cli/data/plugin_api.json`, `website/docs/developer-guide/plugins/kanban-validation.md`, `public-api.md`.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_kanban_needs_you_contract.py`: truth table over status, kind, request completeness, stale, projected wait, owner-device and passive checks; blocked-to-scheduled drops stale kind and classifies as time.
- `tests/hermes_cli/test_kanban_lanes_flow.py`: the `you` case now uses a complete request.

Not proven here: the companion session-panel `human_owned` consumer, its contract/attention/blue-total tests in hermes-mods, and final Mac red-state parity. The implementation handoff reports a backend-startup prerequisite blocking that final Mac check; this mirror did not independently exercise it. This mirror adds no lane implementation beyond the mirrored source change.

## Applying

Use the complete segmented replay recipe in `core/README.md` (Applying), not a standalone `git am`. Companion panel code must call the same predicate to stay consistent; replacing only core changes the lane side. Live deployment is unproved.

## Gotchas

Only cards that currently satisfy the predicate go red; older cards that relied on a bare `block_kind` will stop doing so. Legacy device probes are recognised by bounded helper logic, not a general approval signal. The `attention` row duplicates card fields on purpose for unlinked sidebars; its wait text comes from the last park event only.

## Why not a plugin

The predicate is consumed by core lane classification and published through the plugin API as a read-only name. Plugins can call it but cannot replace the classifier inside the core snapshot, which is what must agree with it.
