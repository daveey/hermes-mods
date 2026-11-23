# Shared strict-link rebuilds and targeted publication lookup

Source patch ordinals: 5743, 5827–5828, 5832.

## Behavior

Queued strict linked-card readers may share a projection that started after their arrival instead of each rebuilding serially. Publication lookup fetches the current projection's wanted card keys rather than indexing every replica task. Dashboard observer and fallback readers share a guarded opening boundary.

## How it works

`StartTickets` assigns reader-arrival and projection-start tickets under a dedicated allocation lock. Publication/freshness state is maintained under the existing links lock. A reader arriving after a projection started cannot use that start ticket as read-your-writes proof; a later projection can answer queued earlier readers despite fingerprint movement.

Publication discovery uses exact `<profile>/<board>/<task id>` keys across listed gateways, without relying on announced scopes. Projection-local results are reusable only within that projection; payloads refresh between projections and out-of-wanted requests get exact fallback lookup. `plugins/kanban/dashboard/board_rows.py::_open_reader` centralizes guarded `open_ro` use for persistent observers and fallback reads.

## Code

- `hermes_cli/kanban_links_tickets.py`
- `hermes_cli/kanban_links_diagnostics.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_origin_ledger.py`
- `plugins/kanban/dashboard/board_rows.py`

The obsolete `hermes_cli/kanban_publication_index.py` full-membership memo is removed by this sequence.

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_links_single_flight.py` covers queued readers sharing one rebuild and rejection of projections started too early. `test_kanban_origin_publications.py` and `test_kanban_links_reason_integration.py` cover wanted-only queries, exact fallback, fresh payloads, deletion/reinsertion, gateway membership changes, malformed keys and read failures. The obsolete `test_kanban_publication_index.py` suite is removed with its implementation. Ordinal 5743 is a reader-boundary refactor with no new test file in that source commit.

## Applying

Apply the reader boundary and projection/ticket changes in source order with their existing freshness dependencies. Do not replace strict semantics with a generic time-only memo.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Ticket sharing proves start-after-arrival, not an atomic multi-store snapshot. It does not bound history scans or authorize stale projections for later arrivals. Publication lookup optimization preserves owner/replacement semantics rather than accepting an old removed owner's completion.

## plugin?

The dashboard reader boundary is plugin code, but tickets and publication authority live in core. Installing a separate plugin alone cannot reproduce the full change.
