# Busy gateway chats admit remote notices to their FIFO

Patches: **2891, 2892, 2893, 2904**.

## Behavior

A busy gateway-owned local chat admits remote Kanban notices into its ordered FIFO instead of relying on a Desktop sweep finding it idle. Custody follows portable notice keys through admission, restart, transcript persistence and folding with local wakes.

## How it works

- **2891:** add a notifier-tick remote-owner leg for busy owned LOCAL chats. Claim notices, admit them as `kanban_notice` with `kanban_event_keys`, and keep the ledger claim in flight until a transcript row carries the key. Refused or dropped admissions release, never acknowledge. Successors acknowledge written rows or adopt pending keys without a second admission. The Desktop sweep stands aside only while the FIFO actually holds the key. Log a written row only after persistence succeeds.
- **2892:** collect with `per_link=True` so the gateway and sweep claim under the link’s exact session even after compression. Already-written lineage members are acknowledged, and unheld/unwritten members of partial groups are released. Add `kanban_cards` so local and remote wakes fold at the FIFO tail in either order, carry all keys onto one notice row and remove keys from cancelled folded-away rows.
- **2893:** a remote notice with no guidance recovers the waiting batch’s local guidance exactly once at the end; add independent written-member and cancelled-key assertions.
- **2904:** test-only probes ensure a local wake folding into a remote-only batch keeps its own guidance, and later remote folding preserves it past relabel Note lines and claim-time drain.

## Code

- `gateway/kanban_remote_owner.py`
- `gateway/kanban_wake_fold.py`
- `gateway/kanban_watchers.py`
- `gateway/response_filters.py`
- `gateway/session_automation.py`
- `tui_gateway/kanban_remote_fifo.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_unloaded.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_remote_owner_delivery.py` covers busy delivery, sweep cooperation, admission refusal/drop, restart before/after persistence, idle/no-owner fallback, compressed-tip links in both orders, partial groups, local/remote FIFO folds, metadata validation and guidance preservation. The fold regressions exercise real admission/store paths with test fixtures, not live remote gateways.

- `tests/gateway/test_kanban_remote_owner_delivery.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

FIFO admission is not transcript delivery: never acknowledge just because it queued. Read pending keys before checking the transcript to avoid releasing a just-persisted notice. Ledger keys use each link’s own session, while duplicate detection spans the compression lineage. Idle chats retain the sweep path; this is not a blanket remote-notice takeover.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
