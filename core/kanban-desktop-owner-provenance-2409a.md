# Scoped Desktop creation provenance for Kanban ownership

Mirror entries: [2304](patches/2304-daveey-fix-kanban-classify-local-Desktop-owners-by-scoped-creation-provenance.patch).

## Behavior

- Retain interactive Desktop/CLI/TUI/ACP/GUI owners whose local transport IDs resemble headless receipts.
- Use gateway/profile-scoped creation provenance for creator links, reconciliation, replica task lists and notice fanout.
- Suppress known machine sessions and unknown receipt-shaped hashes; a foreign reference never borrows a local session's proof.

## How it works

kanban_session_provenance resolves source and created_source, with machine creation winning over apparent transport. Explicit profiles read only their store; legacy unqualified IDs search local profiles conservatively and treat conflicting proof as unknown. Reads are read-only, avoid store creation/migration and cache readable results for 300 seconds. Link and notification callers share the classifier. Fanout trusts the supplied destination store rather than the ambient process profile.

## Code

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_db_notify.py`
- `hermes_cli/kanban_session_provenance.py`
- `tui_gateway/notice_fanout.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_desktop_local_owners.py` — `test_desktop_hash_is_not_headless`, `test_local_row_cannot_prove_foreign_provenance`, `test_ledger_keeps_desktop_origin_and_shepherd`, `test_ledger_profile_is_not_ambient_profile` (additional cases in this file).
- `tests/hermes_cli/test_kanban_desktop_local_replica.py` — `test_desktop_ledger_link_reaches_task_list_and_active_count`.
- `tests/hermes_cli/test_kanban_session_provenance.py` — `test_interactive_local_surfaces`, `test_machine_creation_wins`, `test_foreign_malformed_and_conflicting_refs_never_read_local_store`, `test_legacy_store_and_cache_expiry` (additional cases in this file).

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

A local- transport prefix alone proves neither human interaction nor headless execution. Remote/ambiguous provenance is not upgraded to interactive by guessing. Store readability and cached provenance do not authorize unrelated sessions; no cross-gateway lookup is performed here.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
