# Kanban hub and worker-lease hardening: probe writes, uid-bound completions, closed chat links, assignee fence

Patches: **3605, 3609, 3611–3612**.

## Behavior
- A held card (held terminal by its event log, e.g. a stale scheduled row of a done card) whose wake probe runs on another gateway that has gone silent no longer writes a wake-episode barrier on every sweep. It gets one `stale_wake_ignored` note and no lost-wake event. A live card's silent remote probe still wakes it.
- A hub card completed on a pool worker is verified on the hub. Completion receipts (`pr_acceptance`, `goal_verified`) now carry a stable uid (`*_event_uid`) that survives the Board feed, so the hub can bind them even though event integer ids differ per board file. Pre-uid completions replayed from another gateway fall back to the latest receipt of that kind written by the same gateway in the same run before the completion. A completion naming no receipt never borrows one; a local completion with a wrong id stays unverified.
- `hermes kanban chat-tasks` (which asks for done/archived cards) keeps a chat's released link to a closed remote card as an explicit row (status done or archived, `released=True`, `unfinished=False`, `release_reason=link_released`) instead of dropping it. Sidebar counts and handover read open statuses only and are unchanged.
- Dashboard assignee writes (`PATCH /tasks/{id}` and `POST /tasks/bulk` with `assignee`, with or without `reclaim_first`) on a card another machine runs under a live Board lease are refused: HTTP 409 for PATCH, a per-id `ok: false` for bulk, using the same `reassign` refusal text as the CLI plus the holder, and the database is left unchanged. A mixed patch (assignee plus title or archive) writes none of it. Expired leases, the own gateway's lease and local cards assign as before.
- 3605 is a test-only fix: a hub-admission test stamps its verdict at the Board's holder claim, not the later local lock claim, so a second boundary between the two no longer makes it flaky.

## How it works
- 3605 changes the timestamp update in the test so `MAX(created_at)` is taken over `claimed` events whose payload holder is the hub.
- 3609 hoists the `skip_wake` import to the top of `restore_missing_checks` and calls `skip_wake(conn, tid, LOST_EVENT, park_event_id=park_id)` in the remote-silent branch before `_lost_wake`, as the local restore branch already does.
- 3611 adds `hermes_cli/kanban_completion_refs.py` (`stamp_uids`, `read_candidates`, `origin_of`, `pick`); `complete_task` stamps uids into the completion payload before appending the event. `read_completions` (`kanban_completion_inputs.py`) and `observe_owner_task` plus `completion_verdict` (`kanban_origin_ledger.py`) bind receipts through `pick`, by uid when the payload has one, else by id with the same-run fallback for replayed completions. It also adds `kanban_chat_links_released.py` so `replica_cards` keeps released links to closed remote cards for readers that ask for closed statuses.
- 3612 changes `patch_verb` in `plugin_api_remote_lease.py` so any non-null `assignee` returns `"reassign"`, and a non-fenced status value no longer yields a verb. It builds on the status/reclaim fence from the earlier lease patches.

## Code
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_completion_refs.py`
- `hermes_cli/kanban_completion_inputs.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_chat_links_released.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_origin_ledger.py`
- `plugins/kanban/dashboard/plugin_api_remote_lease.py`
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`
- `tests/hermes_cli/test_kanban_chat_links.py`
- `tests/hermes_cli/test_kanban_remote_lease_dashboard_assignee.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_admission_claim.py`: `test_unjudged_claims_is_empty_before_the_gate_and_ignores_later_verdicts` (modified).
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_a_held_card_whose_remote_probe_went_silent_writes_no_barrier_per_sweep`.
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`: `test_a_worker_completion_replayed_on_the_hub_is_verified_there`, `test_a_pre_uid_completion_replayed_from_a_worker_binds_its_runs_receipt`, `test_a_completion_that_names_no_receipt_never_borrows_one`, `test_a_uid_that_names_no_receipt_of_this_card_does_not_fall_back`, `test_a_local_completion_with_a_wrong_id_stays_unverified`.
- `tests/hermes_cli/test_kanban_chat_links.py`: `test_a_released_link_to_a_closed_card_stays_listed_as_done_for_a_reader_of_closed_cards`.
- `tests/hermes_cli/test_kanban_remote_lease_dashboard_assignee.py`: `test_patch_assignee_under_a_remote_lease_is_refused_and_writes_nothing` (assign, unassign, with-title, with-archive), `test_bulk_assignee_under_a_remote_lease_is_refused_per_id_and_writes_nothing`, `test_bulk_assignee_refusal_of_one_card_does_not_stop_its_siblings`, `test_patch_assignee_without_a_live_remote_lease_assigns_as_before` and `test_bulk_assignee_without_a_live_remote_lease_assigns_as_before` (expired and own-gateway leases), `test_patch_assignee_on_a_local_card_assigns_as_before`.
- Untested by these patches: the origin-ledger release path as a separate entry point (only covered indirectly if the listed completion-refs tests reach it).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3612 is stacked on the earlier dashboard lease fence (`plugin_api_remote_lease`); it needs that patch applied first.
- Completions written before uids existed rely on the same-gateway, same-run fallback; a replayed completion whose integer id happens to name this card's own receipt is bound by that id.
