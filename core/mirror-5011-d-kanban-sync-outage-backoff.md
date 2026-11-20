# Kanban sync: failed-round backoff and one host card per host-wide outage

Patches: **4668, 4669, 4670, 4681**

- 4668 (`5f23461b2b`): [daveey] kanban sync: back off failed rounds; one host card for a host-wide Board outage
- 4669 (`3b075d49b1`): fix(kanban): defer repair wakeups with failed round backoff [daveey]
- 4670 (`1ed8b097d2`): fix(kanban): scope shared outage owners to originating host [daveey]
- 4681 (`81664154b2`): fix(sync): preserve loop early-exit telemetry labels [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- A board whose last sync round failed backs off 1 s doubling to 60 s across every leg (pending outbox, dirty flag, poll, full recovery, deferred repairs, backlog). A pending outbox no longer ends the wait tick. A success, or the feed socket reconnecting, clears the backoff. Queued writes stay queued; healthy boards are unaffected.
- Failures are classified from the exception object (name-resolution failure as dns; refused/reset/unreachable/timeout as network; else unclassified). Boards that come due in one check with a classified recent cause share a single host card listing every board and frozen card; later boards are added by comment; recovery ends the incident and a recurrence opens a fresh one. Unclassified or no-recent-round boards keep per-board cards; archived boards are not judged.
- Deferred repair wakeups respect the failed-round backoff instead of spinning after a post-pull failure.
- The shared-incident idempotency key includes the originating hostname so replicated incident rows from another host are never adopted.
- Loop CPU telemetry keeps correct labels on early exit (schema-ahead give-up vs real exceptions).

## How it works

- `kanban_sync_work.py` tracks per-board failure counts and next-due times; `kanban_sync_client.py` feeds failures and success into it. `kanban_sync_host_outage.py` is the new incident coordinator with persisted-per-boot state; `kanban_sync_replica_age.py` and `kanban_sync_outbox_age.py` route classified failures to it.
- For telemetry, the active service is owned via an `ExitStack` in the caller's lexical scope so early return exits cleanly and exceptions propagate unchanged; `kanban_sync_cpu.py` adjusted accordingly.

## Code

- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_host_outage.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_replica_age.py`
- `hermes_cli/kanban_sync_work.py`
- `hermes_cli/kanban_sync_cpu.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/hermes_cli/test_kanban_sync_cpu.py`: `test_iteration_completion_counts_and_predicate_boundary`
- `tests/hermes_cli/test_kanban_sync_host_outage.py`: `test_ten_boards_failing_on_one_dns_cause_file_one_card_naming_every_board_and_frozen_card`, `test_an_unrelated_board_failure_keeps_its_own_card`, `test_an_archived_board_is_not_judged`, `test_a_board_without_a_recent_round_keeps_its_own_bounded_card`, `test_unclassified_failures_on_every_board_stay_per_board_and_bounded`, `test_a_board_joining_later_is_named_on_the_same_card`, `test_a_restart_mid_incident_keeps_the_same_host_card`, `test_recovery_then_recurrence_is_a_fresh_incident`, `test_the_real_hub_classifies_a_failed_name_lookup_and_keeps_the_outbox`, `test_classify`, `test_a_dns_outage_backs_rounds_off_keeps_the_outbox_and_recovers_promptly`, `test_a_failing_board_never_slows_a_healthy_one`, `test_sync_work_backoff_keeps_dirty_flags_and_clears_on_success_or_reconnect`, `test_deferred_repair_during_post_pull_failure_does_not_spin` (+1 more)
- `tests/hermes_cli/test_kanban_sync_push_starvation.py`: modified only
- `tests/hermes_cli/test_kanban_sync_schema_guard.py`: `test_loop_cpu_early_exit_labels_and_partitions`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- The incident key must stay host-scoped; fleet-wide suffixes cause cross-host adoption.
- Backoff changes retry timing only; do not drop pending outbox rows on failure.
