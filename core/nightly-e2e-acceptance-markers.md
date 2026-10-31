# Nightly E2E acceptance markers and owner-run attribution

Patches: 1383 (`ba77f929233d`), 1384 (`da4cba694371`),
1385 (`8092734fd79e`), 1386 (`8d9612cd7f24`), 1390 (`85ca95df9102`),
1391 (`12369ec78f7c`), 1392 (`e205e8367bdb`).
Builds on [Nightly E2E dispatch and actionable reports](nightly-e2e.md).

## Behavior

- Alerts name the tested ref: failures say `RED on <ref>`, including historical
  refs. Non-main results explicitly do not establish a current-main incident
  and request no main repair. Unmarked main failures remain repair work.
- `--acceptance-marker LABEL --acceptance-owner-task TASK` marks dispatches as
  fixtures, even when testing main. Titles include `ACCEPTANCE[LABEL]`; bodies
  say `acceptance fixture; do not repair main` and request no repair.
  Persisted dispatch intent survives a later report without these flags.
- On `report`, the flags can label previously unmarked **non-main** pending
  entries, including failed, cancelled, timed-out, missing, and lookup-error runs.
  They never relabel unmarked production-main entries or overwrite existing markers.
- Main, individual non-main refs, and acceptance labels have separate card scopes.
  Identical failure sets in different scopes do not update production cards;
  green comments stay in their own scope. A subset is a different fingerprint,
  not a duplicate of the larger failing-test set.
- Fixture creates pair `HERMES_KANBAN_TASK` with the owner's live
  `HERMES_KANBAN_RUN_ID` and pass `--no-requires`. Ordinary creates strip both
  inherited identity variables and retain the default assignee.
- Owner-run selection uses `task.current_run_id` from show JSON, not run-list
  position. It refuses unreadable owners, no live runs, a non-live current run,
  or multiple live runs without a current pointer. One live run without that
  pointer remains a compatibility fallback.
- Kanban show JSON exposes `task.current_run_id`: null before claim and the
  claimed run ID afterward. This series does not change the claim algorithm.

## How it works

`dispatch()` saves `acceptance_marker` and `acceptance_owner` in pending state.
`scope_of()` returns `main`, `ref:<ref>`, or `acceptance:<marker>`; `_scoped_key()`
leaves production keys unchanged and prefixes other scopes. `_file_or_update()`
records scope, stamps fixture ownership, and carries it through a distinct-card
retry. `_settle()` filters green comments by scope, treating old scopeless card
records as main. `_title()`, `_scope_note()`, and `_card_body_main()` separate
ref evidence and fixture instructions from production repair instructions.

`_acceptance()` requires both flags, a marker matching `[\w.-]{1,40}`, and an
owner matching `t_[0-9a-f]{8,}`. If `HERMES_KANBAN_TASK` is set, it must match
that owner. `main()` checks owner existence before dispatch/report.
`NightlyBoard.create()` reads the owner's show payload and selects a run whose
status is `running` and whose `ended_at` is falsey. The pointer comparison uses
string forms of run IDs. The paired environment is an explicit subprocess
addition; `CliBoard._run()` removes inherited task/run identity on creates
without an explicit task override. This changes the shared flaky-board adapter too.

## Code

- `scripts/ci/nightly_e2e.py`: `NightlyBoard.create()`, `dispatch()`, `report()`,
  `_acceptance()`, `main()`, `scope_of()`, `_scoped_key()`, `_title()`,
  `_scope_note()`, `_card_body()`, `_card_body_main()`, `_file_or_update()`,
  `_settle()`, `_settle_error()`.
- `scripts/ci/flaky_kanban.py`: `CliBoard._run()` accepts `extra_env` and
  strips ordinary-create identity (1383–1385).
- `hermes_cli/kanban_output.py`: `_TASK_DICT_FIELDS` feeds `_task_to_dict()`;
  1392 adds `current_run_id` to the shared task serializer.
- `tests/ci/test_flaky_kanban.py`: 1390 adds dependency skips; 1391 restores
  the first affected test's docstring position, with no production-code change.

## Tests

Source tests inspected in the patches/source commits; **not rerun in this mirror**.
In `tests/ci/test_nightly_e2e.py`:

- `test_alert_intent_survives_dispatch_and_report_cli`: fake-backed CLI dispatch
  and report, main/historical ref with and without marker; persisted intent,
  ref titles, body instructions, and fixture creator attribution (1383).
- `test_report_marker_labels_legacy_pending_acceptance`: failure, cancellation,
  timeout, lookup error, and missing run get fixture wording on legacy non-main
  entries (1383, corrected to non-main in 1384).
- `test_report_marker_never_relabels_pending_production_main`: marked report
  leaves an unmarked main incident repairable and without fixture creator (1384).
- `test_acceptance_and_historical_runs_do_not_update_production_cards`: separate
  cards for the same failures in main/ref/acceptance scopes; acceptance green
  comments touch only the acceptance card (1383).
- `test_nightly_board_keeps_auto_assigned_production_routing`: create argv uses
  assignee `default` (1383).
- `test_subset_of_failing_tests_is_a_separate_fingerprint_and_card`: a smaller
  failing set files another card without comments on the first (1383).
- `test_acceptance_flags_are_validated_together`: missing partner flags, unknown
  owner, and invalid marker raise `SystemExit` (1383).
- `test_acceptance_owner_must_be_the_running_worker_task`: foreign owner versus
  inherited task identity is rejected (1383).
- `test_nightly_board_stamps_creator_only_for_acceptance`: paired task/run env,
  missing-live-run refusal, and fixture-only `--no-requires` argv (1383–1386).
- `test_cli_create_env_only_carries_worker_task_in_acceptance`: mocked subprocess
  receives neither inherited identity on ordinary create, both on fixture create
  (1384–1385; extended by the owner lookup stub).
- `test_acceptance_create_uses_current_run_id_from_show_payload`: selects the
  middle current run among three live records (1390).
- `test_acceptance_create_rejects_current_run_that_is_not_live`: rejects stale
  pointer before create (1390).
- `test_acceptance_create_refuses_to_guess_among_several_runs`: no pointer plus
  two live runs refuses before create (1390).
- `test_acceptance_create_clear_error_when_show_returns_none`: unreadable show
  yields explicit failure without create (1390).

In `tests/ci/test_flaky_kanban.py`, 1390 adds `pytest.importorskip("dotenv")` to
`test_cli_board_skips_a_job_a_done_card_already_cites` (real CLI skips already
cited done-card jobs, links a new-job card) and
`test_flaky_test_passes_and_files_a_card_while_a_hard_failure_stays_red`
(real runner retains hard-failure exit 1; only retry-passing test gets a card,
repeat appends). These are existing tests, not new acceptance-attribution proof.
1391 only restores the first test's docstring before executable statements.

In `tests/hermes_cli/test_kanban_show_current_run.py`,
`test_show_json_round_trips_current_run_id` creates/claims a task and calls the
real in-process `kc.run_slash("show ... --json")` path: null before claim,
matching pointer and running run record afterward (1392), not a subprocess test.

Untested here: live GitHub dispatch/report, full fixture-create CLI attribution
and owner cleanup end to end, external scheduling, acceptance distinct-card retry,
and exhaustive validation/marker-overwrite cases. Shared serializer effects on
other JSON consumers have no dedicated test in these patches.

## Applying

These are follow-ups, not standalone patches. Start with the existing nightly
reporter and Kanban infrastructure; see [the base feature](nightly-e2e.md) and
[the mirror application instructions](README.md#applying). Preserve prerequisite
patches from the full mirror, including shared adapter/CLI changes.
Apply via `git am -3` in ordinal order: **1383 → 1384 → 1385 → 1386 → 1390 →
1391 → 1392**, using the matching `core/patches/NNNN-*.patch` files.
1390 consumes the show field supplied by 1392; apply both before relying on
multi-live-run owner selection. No patch application was exercised for this doc.

## Gotchas

- Mark main acceptance runs at dispatch time: report deliberately cannot convert
  an already-pending unmarked main run into a fixture.
- Acceptance scope is keyed by label, not owner or ref; reuse a label only when
  shared deduplication and green comments are intended.
- Fixtures still use normal card creation/assignment; `--no-requires` declares no
  capability needs, not disabled execution. Ownership enables attribution, not
  automatic archival. Cleanup remains the owner's responsibility (untested here).
- Missing `dotenv` skips the two CLI-shelling flaky tests; a bare test environment
  therefore does not establish real CLI coverage. Use the core repository test env.
