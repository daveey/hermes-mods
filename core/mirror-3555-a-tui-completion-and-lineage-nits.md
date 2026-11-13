# Desktop completion obligations and lineage walk follow-ups

Patches: **3532, 3535**.

## Behavior
- Desktop-owned completion obligations avoid reading the receipt file for process ids that never carried one: notice-turn start answers such ids from memory.
- A Desktop obligation for a chat that is never reopened is re-armed on each Desktop start until the recovery or age limit drops it (documented, bounded by design).
- 3535 has no behavior change: the gateway lineage walk and `_session_lineage` share one hop limit by construction, and docstrings describe what they read.

## How it works
3532 adds an in-memory `_tui_owed` set to the process registry, filled when this process writes a Desktop obligation receipt or re-arms one in `_recover_owed_completions_locked`; `settle_tui_completion` answers any other id without touching the receipt. An id leaves the set once its receipt no longer owes it, so a failed settle write is retried. Docstrings now cover Desktop obligations. 3535 makes `session_notifications._session_lineage` default to `kanban_leg_custody.LINEAGE_LIMIT`, corrects the `lineage_ledger_status` docstring ("alphabetically first"), documents that `with_parents` may read a superset of the legacy ids and why that is safe, and drops a signature-parity assertion.

## Code
- `tools/process_registry_obligations.py`
- `tools/process_registry_checkpoint.py`
- `tui_gateway/completion_obligation.py`
- `tui_gateway/kanban_leg_custody.py`
- `tui_gateway/session_notifications.py`
- `tests/tui_gateway/test_tui_completion_obligation.py`
- `tests/gateway/test_kanban_tui_sibling_custody.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_tui_completion_obligation.py`: `test_notice_turn_start_reads_only_receipts_this_backend_owes`.
- `tests/gateway/test_kanban_tui_sibling_custody.py`: 3535 removes a signature-parity assertion from a nit3 test; the depth test `test_the_gateway_walks_as_many_parents_as_the_legacy_lineage` remains. The shared lineage-limit default itself is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Re-armed obligations for chats that are never reopened keep waking until the recovery/age cap drops them.
