# Kanban session provenance: reads_local_store shares scoped_sources routing

Patches: **3318, 3342**.

## Behavior
- `reads_local_store` answers exactly when `scoped_sources` reads the local store, including for empty refs, empty-sid qualified refs, and refs whose outer gateway/profile contradict the scope; an unnamed host's replica premise is pinned by test.
- `local_gateway=None` resolves the same way in both functions and `prefetch_sources`.

## How it works
- 3318 factors a shared `_local_scope` helper, adds the profile scope parameter and updates the callers.
- 3342 introduces one `_resolve_local` resolver for the three functions.

## Code
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_session_provenance.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links_bulk_provenance.py`: `test_backfill_resolves_replica_links_on_a_host_without_a_gateway_name`, `test_reads_local_store_is_exactly_when_scoped_sources_reads`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3342 notes no live impact: both callers pass an explicit name.
