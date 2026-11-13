# Kanban dashboard refuses writes under another machine's live lease

Patches: **3580**.

## Behavior
- Dashboard status writes on a card that another machine runs under a live Board lease are refused with HTTP 409 and nothing is written locally: `PATCH /tasks/{id}` status, `POST /tasks/bulk` status (and assignee with reclaim), `POST /tasks/{id}/reclaim`, `POST /runs/{id}/terminate`, `POST /tasks/{id}/reassign`.
- The 409 detail carries `message`, `holder`, `rejection: live_claim` and `code: remote_live_lease`.
- Bulk operations record a per-id `{ok: false, holder, error}` and skip every op for that id; sibling ids still apply.
- Title edits, unknown-status 400s, expired leases and this gateway's own lease behave as before. The archive route keeps its stop-request path (untested).

## How it works
3580 calls `kanban_remote_stop.remote_holder()` before any write in each endpoint. The helpers live in the new `plugin_api_remote_lease.py` sibling because `plugin_api.py` is at its line cap; the dashboard archive helper moved there too. The boundary-lint baseline is updated for the moved `kanban_remote_stop` imports.

## Code
- `plugins/kanban/dashboard/plugin_api.py`
- `plugins/kanban/dashboard/plugin_api_remote_lease.py`
- `scripts/boundary_lint_baseline.json`
- `tests/hermes_cli/test_kanban_remote_lease_dashboard.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_remote_lease_dashboard.py`: `test_patch_status_under_a_remote_lease_is_refused_and_writes_nothing`, `test_patch_status_with_an_assignee_writes_neither`, `test_bulk_status_under_a_remote_lease_is_refused_per_id_and_writes_nothing`, `test_bulk_refusal_of_one_card_does_not_stop_its_siblings`, `test_reclaim_endpoints_under_a_remote_lease_are_refused`, `test_patch_title_under_a_remote_lease_still_works`, `test_unknown_status_under_a_remote_lease_is_still_a_400`, `test_patch_done_without_a_live_remote_lease_completes_as_before`, `test_bulk_ready_without_a_live_remote_lease_moves_as_before`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The dashboard imports the core guard directly because no published plugin API exists for it.
