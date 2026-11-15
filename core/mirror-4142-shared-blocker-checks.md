# Shared blocker check lane, recovery fences and independent parks

Mirror snapshot 4142; ordinals 4096–4103, 4124, 4125.

## Behavior

- A Kanban "shared blocker" is an external wall (for example a rate-limit quota) that several cards park behind. Each blocker id can now have a registered cheap check: a shell command the dispatcher runs about every 120 seconds while the wall is open, even if a worker probe is running. Exit 0 records recovery and releases the held cards in the same maintenance pass. Exit 1 keeps the wall open. Any other result (other exit codes, timeout, spawn error) counts as an error; three consecutive errors stop the cheap check for that episode and leave the existing worker-probe interval in charge.
- One built-in check exists, for `github-rest-quota`: `python3 <Hermes home>/bin/github_quota.py --reserve-ok`. It depends on a separately installed helper that supports that flag. Config can override or disable it (empty string) via `kanban.shared_blocker_checks`.
- Blocker ids that are historical spellings of the GitHub quota wall all normalize to `github-rest-quota`. `kanban.shared_blocker_aliases` adds one-hop alias mappings for other ids, but cannot split the GitHub family.
- Re-asserting a wall no longer postpones its worker probe. The probe interval counts from the latest probe start, or the oldest assertion still behind the wall if no probe has started.
- Workers see different context for a wall with a registered check: they are told to run the live check when their next step needs the capability, and not to re-assert from context alone.
- For the GitHub quota wall, an assertion from a run that started before a verified recovery cannot reopen the wall. The card stays releasable. A run that started after the recovery can still report a new outage.
- An operator's explicit unblock of a card on a checked wall is honored even if a peer card is running or the wall was re-asserted by another card. A fresh assertion from that same card consumes the release.
- A card parked on its own independent wait (its own probe/backstop, or a different blocker) is not woken or rewritten when an unrelated shared wall clears. It only receives an annotation event.
- Shared-wall clear annotations no longer count against, or reset, the "parked 3x on the same premise" re-park budget.
- Re-park escalation now creates a ready card for the parking card's own assignee (or `default`) when no distinct supervisor is available, instead of an unassigned triage card.
- Entry paths: the check lane runs in the dispatcher's shared-blocker maintenance sweep. No terminal, execute_code, tool, REST or Desktop entry path is added; none are tested beyond what is listed under Tests.

## How it works

- 4096: new `kanban_shared_blocker_checks.py`. `run_due_checks` runs outside any write transaction (it raises if called inside one), launches the command through `bash -c` in a new session with a scrubbed environment, a 10-second timeout and a 500-character output tail, then appends a `shared_blocker_check` event on a home-owned card in the wall. Cards owned by another gateway are skipped, so evidence is only written by the home side. The write is re-validated under the write transaction: if the assertion changed or ownership moved during the command, the result is dropped. `apply_check_clears` folds a stored rc 0 into derived blocker state (the event is evidence, not the clearer) only when its assertion token still matches. The dispatcher registers a gated `dispatch.shared_blocker_checks` step before the release and probe lanes, and contributes the next due time to the maintenance gate. `normalize_blocker_id` gains the GitHub quota alias collapse. The probe and guard interval now use the oldest assertion behind the wall instead of the newest. Rendered blocker context adds the re-check instructions.
- 4097: adds a subprocess-lint annotation on the spawn and adjusts test fixture imports.
- 4098: the assertion token becomes a SHA-256 fingerprint of the portable identities (synced run UID, else task/start/end/blocker) of every run at the latest assertion timestamp, so it survives integer id remapping and same-second runs. Check and assertion scans are bounded by the lookback window, and older assertions aging out do not revoke a stored success. Backoff belongs to the open episode: re-assertions neither reset the error count nor postpone the next check; a command change, a success, or a clear starts a new episode. Payloads now carry `started_at`, `checked_at` and `assertion_id`. New `kanban_shared_blocker_fence.py` keeps stale GitHub quota assertions from reopening a verified recovery. `shared_blocker_aliases` config and read-time normalization of historical event payloads are added, and `_unblocked_since_assertion` is rewritten to scan only events after the latest unblock. Adds `docs/shared-blocker-recovery.md` and the two config defaults.
- 4099: adds a replay test and JSON fixture built from two exported incident histories (the central hub's run history and a sync-shadow copy), retaining only wall metadata. The test replays run rows tick by tick (120-second ticks plus row-arrival ticks), with a modeled check that returns 1 before a reported reset time and 0 afterward, and asserts that no wall remains open from the reset plus 120 seconds onward. Its own docstring says lanes, transport and check results are modeled, so it is a counterfactual replay and not proof of natural recovery.
- 4100: a discarded (raced) check run now writes a `shared_blocker_check_attempt` event that keeps the 120-second cadence but is never treated as evidence. Check receipts record a snapshot of runs open at write time. The fence uses that snapshot so a run admitted while the command was running stays fenced, while a same-second claim made after the receipt can reopen the wall; receipts without a snapshot fall back to strict timestamp comparison. Equal-second conflicting results fail closed (a failure beats a success). The default command gets a `python3` prefix so a non-executable helper still runs. A malformed `kanban` config section disables checks.
- 4101: `shared_blocker_guard` returns no hold when the blocker has a registered check and the card's own events show an unblock after its own latest assertion (`own_only` option), so peers' assertions cannot revoke the release.
- 4102: the fence selects the winning receipt per second, so losing tied successes no longer count. Receipts add `checked_at_ms`. For same-second claims absent from the local snapshot (for example a remote claim not yet synced), a UUIDv7 run identity can show the claim began strictly earlier in milliseconds, but only when the claim second, start second and check second agree; otherwise the older fallback applies. Docs note this is wall-clock evidence, not distributed causal ordering.
- 4103: documents the two settings in the user-guide configuration page.
- 4124: `release_cleared_blockers` checks the card's current park. A scheduled card with its own wake check and a different (or no) shared-blocker marker only gets a cleared annotation event, like ready/review cards. `prior_same_premise` now reads cleared events: only one that actually woke the card (status ready or review) ends the streak; annotations are skipped. `escalate` falls back to the card's assignee or `default` and no longer sets triage.
- 4125: extends the park-wake test with an awaiting-only marker and a no-probe case, and asserts that both are released.

## Code

Source paths touched by this feature group (including regression fixtures):

- `docs/shared-blocker-recovery.md`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_idle_candidates.py`
- `hermes_cli/kanban_shared_blocker_checks.py`
- `hermes_cli/kanban_shared_blocker_fence.py`
- `hermes_cli/kanban_shared_blockers.py`
- `tests/fixtures/shared_blockers/2026-11-14.json`
- `tests/hermes_cli/test_kanban_awaiting.py`
- `tests/hermes_cli/test_kanban_blocker_park_wake.py`
- `tests/hermes_cli/test_kanban_shared_blocker_check_transport.py`
- `tests/hermes_cli/test_kanban_shared_blocker_checks.py`
- `tests/hermes_cli/test_kanban_shared_blocker_livelock.py`
- `tests/hermes_cli/test_kanban_shared_blocker_operator.py`
- `tests/hermes_cli/test_kanban_shared_blocker_races.py`
- `tests/hermes_cli/test_kanban_shared_blocker_remote_race.py`
- `tests/hermes_cli/test_kanban_shared_blocker_replay.py`
- `tests/hermes_cli/test_kanban_shared_blocker_result_order.py`
- `tests/hermes_cli/test_kanban_shared_blocker_uid_precision.py`
- `website/docs/user-guide/configuration.md`

Mail entries in the ordered series:

- 4096: [`350fafc36d43081defe0fbd6f600719a31ff8c69`](patches/4096-daveey-WIP-shared-blocker-check-lane-and-stable-prob.patch)
- 4097: [`c7d6edfe85a19463fa39da23f9aa1c3f03d5ea90`](patches/4097-daveey-Satisfy-checker-fixture-and-subprocess-lint-g.patch)
- 4098: [`0eec04e7d7aa3653765266fbfaaf69c5673e9cc3`](patches/4098-daveey-Fence-stale-quota-assertions-and-persist-port.patch)
- 4099: [`13a3bec9e72bf2d226797e2546f6fe4e38ea4c09`](patches/4099-daveey-Replay-real-hub-and-shadow-quota-wall-history.patch)
- 4100: [`c51bfbc40bbc1447857958add9c90f3a5cdd48be`](patches/4100-fix-kanban-preserve-cheap-check-cadence-and-same-sec.patch)
- 4101: [`018c219209a208cb1d3af1a5aa0619c39abe5e18`](patches/4101-fix-kanban-honor-operator-release-before-cheap-wall-.patch)
- 4102: [`388e81fb72cdf4b7e001598f4dce9c4089ec2dcb`](patches/4102-fix-kanban-preserve-authoritative-check-ordering-acr.patch)
- 4103: [`6a76033b53f42a5ddf5a5f005cdf6c802c7cc7b1`](patches/4103-docs-kanban-document-shared-blocker-recovery-setting.patch)
- 4124: [`c18824850eac35bb69afa5f260f6ab13e34cc682`](patches/4124-daveey-preserve-independent-parks-across-shared-bloc.patch)
- 4125: [`4711fcfa43433976fa2dccbeb12589fa766a8a4b`](patches/4125-daveey-cover-explicit-shared-awaiting-and-no-probe-w.patch)

## Tests

All tests below exercise the dispatcher/Kanban library paths directly; none go through terminal, execute_code, tool-call, REST or Desktop entry points, so those paths are untested here (the repark tests in `test_kanban_awaiting.py` call the schedule tool helper, which is the only tool-path coverage).

`tests/hermes_cli/test_kanban_shared_blocker_checks.py`:
- `test_success_is_durable_even_with_worker_probe_running`: rc 0 clears the wall with `cleared_via == "check"` while a worker run stays running; output is UTF-8 and stored.
- `test_success_survives_older_assertion_aging_out`: an older assertion leaving the lookback does not reopen the wall.
- `test_worker_started_during_successful_check_is_stale`: a run claimed during the check cannot reopen the GitHub wall and is released.
- `test_not_yet_persists_cadence_across_connections`: rc 1 keeps the wall open; next run is not before 120 s, across connections.
- `test_three_errors_stop_checks_without_clearing`: three errors stop checks, log the fallback, and a corrected command recovers immediately.
- `test_home_authority_uses_foreign_ids`: no check evidence is written for a foreign-owned card.
- `test_reassert_during_check_discards_stale_success`, `test_later_same_second_assertion_is_not_cleared_by_old_check`: a new assertion invalidates an older success.
- `test_default_is_profile_scoped_and_overrides_can_disable`: default command is under the profile's Hermes home; empty override disables it.
- `test_shell_runs_outside_transaction_with_utf8_and_scrubbed_env`, `test_check_timeout_is_an_error_not_a_clear`, `test_unavailable_config_skips_commands_instead_of_killing_tick`, `test_check_refuses_caller_write_transaction`: execution environment, timeout, config-backend failure and transaction guard.
- `test_assertion_identity_survives_remapped_run_ids`, `test_assertion_scan_is_bounded_by_lookback`, `test_non_probe_reassertion_neither_resets_errors_nor_postpones`, `test_new_episode_after_clear_recovers_from_broken_checker`: portable token, bounded scan, episode-scoped error budget.

`tests/hermes_cli/test_kanban_shared_blocker_livelock.py`:
- `test_github_wall_has_one_identity` (parametrized): alias spellings normalize to `github-rest-quota`.
- `test_config_alias_and_legacy_event_reader`: configured alias maps one hop; old spellings in events are read correctly.
- `test_new_assertions_do_not_postpone_oldest_probe`: probe due time follows the oldest assertion.
- `test_dispatch_tick_clears_all_aliases_and_carrier`: one sweep via `sweep_shared_blockers` clears every alias, releases all cards and emits `cleared_via == "check"`.
- `test_pre_clear_run_cannot_reopen_quota_wall` (parametrized by check or non-check clear): pre-clear run stays releasable; a later run can reopen.
- `test_non_probe_context_requires_live_check`: rendered context names `--reserve-ok` and the no-re-assert rule.
- `test_promoted_card_after_reset_checks_before_continuing`: a card promoted after reset sees the check instruction, runs it, and the wall closes.

`tests/hermes_cli/test_kanban_shared_blocker_replay.py`:
- `test_incident_run_replay` (parametrized `hub`, `shadow`): replays the exported history with modeled lanes and check results; asserts assertions and releases occurred and no wall is open from reset plus 120 s.

`tests/hermes_cli/test_kanban_shared_blocker_races.py`:
- `test_same_second_claim_after_check_reopens`, `test_next_second_claim_after_check_reopens`: later claims can report a new outage.
- `test_same_second_admitted_during_command_stays_fenced`: claims admitted mid-command stay fenced.
- `test_receipt_records_active_run_snapshot`, `test_older_receipt_uses_strict_timestamp_fallback`: snapshot field and its fallback.
- `test_raced_check_keeps_cadence_without_clearing`: a discarded run writes only an attempt event and holds the cadence.

`tests/hermes_cli/test_kanban_shared_blocker_check_transport.py` (in-memory Board with two gateways):
- `test_only_home_checks_and_portable_evidence_roundtrips` (parametrized by order and rc 0/1/2): only the home side checks, evidence syncs with matching tokens, release happens only for rc 0 on home, and the host-local probe is untouched.
- `test_same_second_reassertion_invalidates_transported_success`.

`tests/hermes_cli/test_kanban_shared_blocker_result_order.py`:
- `test_equal_second_failure_wins_over_success` (parametrized by order and blocker id): a tied failure wins and nothing is released.
- `test_malformed_section_disables_checks`.

`tests/hermes_cli/test_kanban_shared_blocker_operator.py`:
- `test_operator_unblock_survives_peer_running_and_reassertion`: guard returns none after an explicit unblock; a fresh own assertion restores the hold.
- `test_cheap_check_releases_waiter_without_waiting_for_long_run`.

`tests/hermes_cli/test_kanban_shared_blocker_remote_race.py`:
- `test_unsynced_same_second_claim_order` (parametrized before/after): a remote claim before the check is fenced and released; one after reopens the wall.

`tests/hermes_cli/test_kanban_shared_blocker_uid_precision.py`:
- tests for `_uid_before_check`: invalid or non-v7 UUIDs, missing/invalid millisecond precision (including booleans), inconsistent seconds and the strict-earlier rule.
- `test_default_registered_check_runs_nonexecutable_stub_in_profile_home_with_spaces`: the default command runs a non-executable stub from a profile home path containing spaces (posix only).

`tests/hermes_cli/test_kanban_blocker_park_wake.py`:
- `test_clear_respects_current_park` (parametrized `None`, `other-wall`, the blocker id, `awaiting-only`, `no-probe`): independent parks are kept untouched with one cleared annotation; same-wall, awaiting-only and no-probe parks are released.
- `test_newer_clear_after_an_independent_repark_retires_only_the_hold`, `test_clear_after_reassertion_keeps_independent_park_after_redundant_clear`: independent parks survive later clears.

`tests/hermes_cli/test_kanban_awaiting.py`:
- `test_third_park_on_the_same_premise_goes_to_the_supervisor`: updated to expect a ready card for the worker's own profile.
- `test_shared_clear_wakes_do_not_exhaust_repark_budget`, `test_clear_annotation_does_not_reset_repark_budget`, `test_escalation_without_any_assignee_uses_default`.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Checks are shell commands run as the dispatcher account; configure only trusted, read-only ones. Output tails are kept in task events.
- The two-minute cadence is not a wall-clock guarantee if the dispatcher is stopped or its maintenance loop is delayed.
- The built-in GitHub check needs the companion quota helper with `--reserve-ok`; this series does not add it.
- Only the GitHub quota family gets the stale-assertion fence; other blocker families keep earlier semantics.
- The UUIDv7 ordering is wall-clock evidence, not distributed causal order. Clock skew, missing precision and exact ties fall back to the older timestamp/snapshot rules.
- The replay fixture is a counterfactual: lanes, transport and check results are modeled, so it does not show natural live recovery.
- Alias targets must be canonical ids; aliases are resolved one hop only.
- Parks with their own wake check, or a different shared-blocker marker, are not woken by an unrelated clear; whether they are eventually woken depends on their own probe.
- This document makes no deployment claim.

## Plugin?

No. This changes the dispatcher's blocker state derivation, release lane and escalation logic in core Kanban modules; a plugin has no hook that owns those transitions.
