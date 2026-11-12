# Hub-probed atomic publication of worker labels

Patches: **2729, 2730**.

Related context: [kanban-operator-call-2563.md](kanban-operator-call-2563.md).

## Behavior

`kanban_supervisor_request` accepts an operator `label_publish` action with `machine`, `labels` and `proof: {kind: 'hub_probe'}`. It adds only `skill:`/`capability:` labels to a machine's Board registry entry; it never replaces the set. One unproven label refuses the entire call. Patch 2730 raises the client-side total-label cap from 32 to the Board's 512, covering a 231-label worker registry.

## How it works

The hub is the executor, not a worker or the requesting gateway. It re-runs its own `label-probe <machine>` and validates the complete inventory/capability evidence, machine identity and namespace. A missing admin key refuses `no_admin_key`; absent/failed probing gives `proof_unavailable`, unproven evidence `bad_proof`, and disallowed namespaces `label_refused`. A pre-read identifies `already_applied`, `unknown_machine` or `too_many_labels`; otherwise one `hermes-mail admin machine <m> --add-labels` uses Board `labels_add` in the server transaction. Concurrent additions/removals survive; a response missing any requested label is a failure. Background `operator_label_jobs` bound attempts and stale work (`JOB_STALE_S=900`, beyond the 330-second worst job), with result delivery backoff and the request's 24-hour deadline. `operator_calls` gains `target_machine`. A result on the requester's own card is accepted only with Board-vouched hub origin.

## Code

- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_operator_labels.py`
- `tools/kanban_tools_schemas.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_operator_call.py`: `test_label_publish_needs_machine_labels_and_proof`, `test_only_one_named_card_per_call`.
- `tests/hermes_cli/test_kanban_operator_labels.py`: `test_accepted_publish_adds_the_delta_and_answers_on_the_requesters_card`, `test_labels_already_there_are_already_applied_without_a_write`, `test_machine_and_other_namespaces_are_refused`, `test_home_revalidates_a_machine_label_smuggled_past_the_requester`, `test_malformed_proof_is_refused_at_submit`, `test_label_the_hub_probe_does_not_prove_is_refused_bad_proof`, `test_incomplete_inventory_or_another_machines_probe_proves_nothing`, `test_unreadable_probe_fails_without_publishing`, `test_hub_without_the_probe_installed_fails_closed`, `test_concurrent_writer_label_survives_the_delta`, `test_concurrent_removal_is_kept_not_undone`, `test_delta_is_sent_as_labels_add_only`, `test_board_answer_without_the_delta_is_a_failure_not_applied`, `test_board_answer_without_a_label_set_is_a_failure`, `test_delta_past_the_board_label_cap_is_refused_without_a_write`, `test_delivery_backs_off_after_the_free_retries`, `test_job_stale_bound_exceeds_the_worst_job`, `test_unknown_machine_is_refused`, `test_forged_result_on_the_requesters_own_card_is_ignored`, `test_unreachable_board_keeps_the_answer_until_it_is_delivered`, `test_replayed_request_is_not_published_twice`, `test_a_job_that_keeps_dying_fails_after_bounded_attempts`, `test_hub_without_the_admin_key_refuses_and_never_runs_an_admin_command`, `test_workers_never_touch_an_admin_key_or_execute_label_publish`, `test_dry_run_reports_the_hub_as_executor`, `test_label_publish_takes_no_card`, `test_result_payload_is_bound_to_this_request`, `test_worker_with_a_full_skill_inventory_is_published_not_capped`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

The older operator-call doc says label publication is not offered; these patches supersede that restriction only. Requires the probe and a Board/client supporting atomic add-labels; never substitute read/replace or client CAS. The admin key remains hub-local; do not put key material, live probe inventories or actual machine identifiers in public examples. Tests use controlled Board/probe jobs, not a live registry or the real supervisor tool transport.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
