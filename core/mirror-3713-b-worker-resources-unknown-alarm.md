# worker_resources_unknown streak alarm and inventory hardening

Patches: **3699–3702**.

## Behavior
- A board deleted under `/dev/shm` (RAM-backed, boot-cleared) retires from the worker account-board inventory like one under `/tmp`, instead of making every claim on the host refuse with `worker_resources_unknown`. A board whose directory still exists but whose file moved still refuses.
- When every claim on a host answers `worker_resources_unknown` for more than 10 minutes, the dispatcher opens one agent repair card per host per incident, placed off that host and naming the inventory record. No Inbox post, no human ask.
- `HERMES_TEST_POLICY_ROOT` lets a non-pytest test harness point the account policy root at its own temp directory so its throwaway boards never register in the real inventory. It is honoured only for an absolute path strictly under a temp root; anything else (relative, durable, the temp root itself, empty) is ignored.
- The alarm's streak file is read as `utf-8-sig`, so a BOM-prefixed file still parses (untested).

## How it works
- 3699 adds `TMPFS_ROOTS = (/dev/shm,)` to the ephemeral roots of the worker account-board inventory.
- 3700 adds `worker_resources_unknown_alarm`: `worker_resource_admission.refusal()` now feeds `observe()` with each answer. A streak (first seen, last seen, failing check, named record) is kept in `worker-resources/unknown.json` under the policy root, rewritten on a new streak, changed detail or every 60 s, and removed by the first other answer. `observe` never raises. `sweep()` (called from the parent-unclaimed dispatcher sweep) files the card once a fresh streak is older than 600 s; a streak idle for over 300 s counts as a new incident.
- 3701 adds `_test_policy_override()`, consulted by `policy_root()`.
- 3702 changes the streak file read encoding to `utf-8-sig`.

## Code
- `hermes_cli/worker_account_boards.py`
- `hermes_cli/worker_resource_admission.py`
- `hermes_cli/worker_resources_unknown_alarm.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/worker_compute_capacity.py`
- `tests/hermes_cli/test_worker_account_boards.py`
- `tests/hermes_cli/test_worker_resources_unknown_alarm.py`
- `tests/hermes_cli/test_worker_compute_capacity.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_worker_account_boards.py`: `test_tmpfs_root_is_ephemeral`, `test_board_removed_from_a_tmpfs_root_retires_like_tmp`.
- `tests/hermes_cli/test_worker_resources_unknown_alarm.py`: `test_refusal_records_the_streak_and_names_the_record`, `test_any_other_answer_ends_the_streak_and_a_gap_starts_a_new_one`, `test_a_streak_past_ten_minutes_files_one_card_off_this_host`, `test_a_stale_streak_files_nothing`, `test_observe_never_raises`.
- `tests/hermes_cli/test_worker_compute_capacity.py`: `test_test_policy_root_is_honoured_only_under_a_temp_root`, `test_policy_root_follows_the_override_in_a_child`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The alarm acts only on a primary board; other board modes file nothing.
- `HERMES_TEST_POLICY_ROOT` is for test harnesses; it cannot relocate a worker's real policy to a durable path.
