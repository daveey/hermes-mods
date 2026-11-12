# Classify origin-ledger sessions once per exact profile

Patches: **2898**.

## Behavior

Origin-ledger chat-link projection avoids per-row profile discovery and state.db classification: eligible session references are bulk-classified once per exact origin profile, with no new cross-call cache.

## How it works

**2898:** filter board/origin/session/placeholder rows first, retain original eligible order, group by `(type(profile), profile)`, then call `machine_sessions` once per group. Test membership using `str(session)` as the classifier does. Preserve link kind, one shared timestamp, output shape and last-write behavior for duplicate UIDs.

## Code

- `hermes_cli/kanban_chat_links.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_ledger_links_bulk.py` compares exact ordered output with the frozen per-row implementation, checks K bulk calls and zero per-row calls for K profiles, observes changed rows/profile data on the next call and retains the unchanged-board early gate.

- `tests/hermes_cli/test_kanban_ledger_links_bulk.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

None, empty and explicit profile values must not be conflated by grouping. Bulk classification changes cost, not link authority, order or release policy. Reported synthetic speedups are source-commit evidence, not rerun mirror benchmarks.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
