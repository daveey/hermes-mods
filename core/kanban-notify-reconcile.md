# Kanban notify-subscription reconcile

Patches 1388 (`f8c39f0e`), 1412 (`d739a5e2`), 1413 (`816682ae`), 1414 (`2b855ee2`).

## Behavior

A worker whose send into a bare `@session` ref was refused, for a chat owned by another gateway, could end up with a `platform='tui'` notify subscription on its own board. Nothing local can drain it. The unseen count then stays above zero and the chat-closeout watchdog files a false "unseen" incident, even though the owner gateway already delivered the result over the hub link.

- 1388 adds `reconcile_refused_sub`. It retires such a row only when it has proof of delivery, and otherwise leaves the row in place so a later attempt can retry.
- 1412 lets a worker request that retirement through the supervisor-request channel. Workers are write-fenced, so a non-worker executor (dispatcher tick or `supervisor-requests --drain`) runs it. The executor asks the owner gateway for receipts through hermes-hub.
- 1413 resolves the hermes-hub binary through the existing `hub_cli_path()` instead of a new env var.
- 1414 marks the executor's two read-only sqlite opens (`state.db`, `hub.db`) as lockguard-exempt.

The feature never sends, wakes a chat, or moves a cursor.

## How it works

**1388 gates (`reconcile_refused_sub`).**
- Stable outcomes: `absent`, `provenance_mismatch` (delivery metadata is not `send_refused`), `inflight`, `local_owner`, `unknown_owner`, `missing_remote_route` (no `hub` sub), `nothing_pending`, `unsupported_events` (anything but `completed`), `missing_receipt`, `evidence_unavailable` (any reader exception), `changed_retry`, `reconciled`.
- The receipt is the exact key `remote:<gw>:<profile>/<board>/<task>:<event>` for every pending event.
- Before removal, inside one writer txn, it re-reads the row snapshot, the pending events and the hub route.
- On success it removes the row and appends a `notify_reconciled` event.
- `remove_notify_sub` now uses `write_txn(allow_nested=True)` so it composes into that outer txn.

**1412 executor.**
- Op `{op: reconcile_notify, task_id, chat_id, thread_id?}`. Evidence fields are rejected. A request carries only reconcile ops or only edge ops.
- Preflight returns `supervisor` for a `send_refused` tui row, `noop` if the row is gone, and `refused` for an unknown task or a different reason.
- There is no creator-scope rule. The owner-receipt gate is the safeguard.
- Phase 1 (`start`): the candidate owner is the task's single `hub` sub on another gateway. It mints one read id per (gw, profile, chat), queues `hermes-hub request <gw> sessions <profile>/<chat> notice-receipts --arg keys=… --id …`, and parks the request as `accepted` with `result_json.phase=awaiting_owner`.
- Phase 2 (`poll`, called from `drain`): reads `hub.db` `sent` rows without spending delivery attempts. Only a `done` answer whose stored envelope and echoed result match the read is used, and it feeds the unchanged `reconcile_refused_sub`.
- `rejected`, `expired`, `failed` and `quarantined` reads, mismatches and an unanswered read after 24 h (`OWNER_DEADLINE_S`) all keep the row.
- A read that was never queued is re-queued with the same id. More than 50 keys per chat (`MAX_KEYS`) rejects the request.
- Results land as `supervisor_request_*` events plus a comment on the requester.
- `kanban_supervisor_requests._accept_next` skips requests in `awaiting_owner`.

**1413.** `_hub_bin()` returns `kanban_capability_registry.hub_cli_path()`, replacing `HERMES_HUB_BIN` / `shutil.which` / the home fallback.

**1414.** Adds `# noqa: kanban-lockguard` to the `mode=ro` connects in `local_session_exists` and `_sent`. These open `state.db` and `hub.db`, not boards.

## Code

- `hermes_cli/kanban_notify_reconcile.py` (new, 1388): `reconcile_refused_sub`, `_pending`, `_hub_sub`.
- `hermes_cli/kanban_db_notify.py` (1388): `remove_notify_sub`.
- `hermes_cli/kanban_notify_reconcile_exec.py` (new, 1412; edited 1413, 1414): `normalize_op`, `label`, `preflight_op`, `publisher`, `local_session_exists`, `_hub_candidates`, `_hub_bin`, `_run_hub`, `_queue`, `_sent`, `_evidence`, `start`, `poll`, `_advance`, `awaiting`.
- `hermes_cli/kanban_supervisor_requests.py` (1412): `OPS`, `normalize_ops`, `preflight`, `_accept_next`, `_awaiting_owner`, `_finish`, `_execute`, `drain`, `op_label`.
- `tools/kanban_tools_schemas.py` (1412): `KANBAN_SUPERVISOR_REQUEST_SCHEMA`.

## Tests

Source tests, not rerun in this mirror.

`tests/hermes_cli/test_kanban_notify_reconcile.py` (1388):
- `test_receipt_retires_only_phantom_without_redelivery` pins missing receipt → kept, receipt → `reconciled`, repeat → `absent`, no extra message, and the hub sub survives.
- `test_unknown_or_undelivered_remains_unseen_and_retryable` is parametrized over local, unknown, unreadable, owner_unreadable, wrong_profile and no_receipt.
- `test_racing_completion_does_not_get_discarded`.
- `test_earlier_event_text_is_not_receipt`.
- `test_receipt_in_compacted_ancestor_counts_once`.
- `test_drained_row_is_left_alone`.
- `test_genuine_local_row_is_not_reconciled_and_stays_unseen`.
- `test_missing_hub_route_keeps_row`.
- `test_row_without_refusal_provenance_is_untouched`.

`tests/hermes_cli/test_kanban_notify_reconcile_exec.py` (1412; 1413 adjusts the fake-hub setup):
- Happy path and keep-the-row cases:
  - `test_owner_receipt_retires_the_rows_and_reports_on_the_requester`
  - `test_no_receipt_keeps_the_row`
  - `test_unknown_owner_session_keeps_the_rows`
  - `test_no_single_remote_route_keeps_the_row_without_asking`
  - `test_unreadable_local_store_keeps_the_row`
  - `test_a_local_chat_keeps_the_row`
- Evidence validation and owner-read failure:
  - `test_an_answer_that_does_not_match_the_read_keeps_the_rows` (envelope, echo_keys, echo_session, extra_delivered)
  - `test_a_failed_owner_read_finishes_at_once_and_keeps_the_rows` (4 states)
  - `test_owner_silent_past_the_deadline_fails_and_keeps_the_rows`
  - `test_new_completion_after_the_read_is_not_retired_on_old_evidence`
- Phase and queue resilience:
  - `test_executor_crash_and_restarts_reuse_the_read_and_spend_no_attempts`
  - `test_a_read_the_hub_cli_could_not_queue_is_queued_again_with_the_same_id`
  - `test_a_preseeded_read_id_conflict_fails_and_keeps_the_rows`
  - `test_two_owner_reads_settle_independently_and_report_what_each_did`
  - `test_a_transient_hub_db_error_waits_instead_of_failing`
- Request shape and the worker fence:
  - `test_submit_refuses_evidence_fields_and_bad_rows`
  - `test_submit_refuses_mixed_ops`
  - `test_preflight_verdicts`
  - `test_a_worker_cannot_run_the_executor_or_the_mutation`
- `test_end_to_end_through_a_separate_executor_process_and_the_hub_cli` runs a fake `hermes-hub` first on PATH. After 1413 this also exercises `hub_cli_path` resolution via PATH only.

Untested paths:
- The schema text change in `tools/kanban_tools_schemas.py`, beyond the e2e tool dispatch.
- The 1414 `noqa` marking, which has no test of its own. Whether the lockguard check accepts it is not shown by these patches.
- `publisher()` mapping for non-default profiles and boards, and the `MAX_KEYS` rejection. Neither has a test named in the patch.
- The hermes-hub client (>= 1.9.24) side. It is outside these patches and only faked here.

## Applying

Apply in order with `git am -3`: 1388, 1412, 1413, 1414.
- 1412 depends on 1388, because the executor imports `reconcile_refused_sub` and `_pending`.
- 1413 and 1414 edit the file 1412 creates.
- The patches assume `kanban_supervisor_requests`, `kanban_capability_registry` (`hub_cli_path`, `hub_db_path`, `self_gateway`) and `SessionDB.kanban_notice_keys_delivered` / `record_kanban_notice` already exist.
- They also assume the owner gateway's hermes-hub understands the `notice-receipts` action.

## Gotchas

- Evidence is only for `completed` events. Any other pending kind keeps the row.
- Resolving the owner before subscribing (the producer fix) lives in hermes-mods session-mentions, not in these patches.
- A request waits up to 24 h for a sleeping owner and then fails with rows kept. Resubmit to retry.
- Only a single other-gateway hub route is eligible. Several routes, or none, fall through to the no-evidence outcomes.
- `local_session_exists` scans every profile's `state.db` on the host. An unreadable store raises and keeps the row.
