# Absent-session ACKs, expired claims and fanout custody

Patch coverage: 6691, 6692, 6693, 6694, 6695, 6711.

## Behavior

Two live code paths change; neither is behind a flag in this series.

**1. Remote card-change fanout (`notice_fanout._remote_change_observed`, per chat).** The session lookup `find_session_db` now yields a `SessionLookup` (still a `(db, tip)` tuple) with an `unavailable` flag. `unavailable` is set when the profile roster is incomplete (6694 reuses the completeness-aware `profile_roster`), a profile store cannot be opened, or a store read raises, and only if no session was found. In `_refresh_remote_chat`:
- `db is None` and `unavailable` (the default when the lookup object is a plain tuple): retry with reason `db_none`, no ACK.
- `db is None` and not unavailable: proven absence. The chat is recorded in the in-process `_remote_absent` map with the snapshot entry and a monotonic time, a `no_local_session` warning is logged once per `(chat, entry)` pair, and the chat is ACKed as an intentional skip.
- a found session: the entry leaves `_remote_absent`; the usual skip reason or outcome refresh applies. A failed refresh gives `refresh_failed`, an exception gives `exception`, both retry without ACK.
- Cached absence is only re-looked-up when the snapshot entry changes, or for chats whose last check is at least 60 monotonic seconds old **and** the pass runs under a host maintenance admission (`maintenance_admitted()`). With a pending health generation, a chat already in `_remote_absent` is ACKed from cache without a lookup.
- Retry sites call `remote_retry(out, reason)`, which emits a bounded `remote.retry` label (`inputs_error`, `initial_poll_failed`, `seed_failed`, `db_none`, `refresh_failed`, `exception`, `unknown`) and schedules the `remote_card_change` subpass; the same reasons were added to `gateway/notification_retry_provenance.REASONS`. A failed durable ACK commit is reported as `refresh_failed`.
- Warning keys are pruned to the current snapshot only when the ACK commits (6694); absent-map entries for chats no longer in the snapshot are dropped at the same point.
- Pending-recheck custody (6695): before refreshing a chat that is in `_remote_absent`, the chat is added to `_remote_pending`; it is removed only after `ack_remote` commits it. While `_remote_pending` is non-empty the memo-hit shortcut, the unchanged-chat skip and `_certify_remote` are all bypassed. The set is process-memory only; the durable ACK ledger remains the cross-restart authority.
- Stage accounting (6692): `Stage`/`stage_accounted` record exclusive thread-CPU per named stage (dispatch, enumeration, local_board, remote_flow, inputs, poll, projection, lookup, outcome_refresh, certification, health) with a `complete`/`exception` outcome under `unloaded.diag.card_stage.*`; parent stages exclude child intervals, and the projection code was moved unchanged into `_remote_projection`.

**2. Remote notice sweep (`kanban_remote_notices._settle_stale_claims`, called from `sweep_remote_notices` with the sweep's `now`).** A claim whose ref is missing from the replica states used to be left alone; it now goes to `kanban_remote_expired_claims.settle_absent`. That function resolves the replica source path, takes `BEGIN IMMEDIATE`, re-reads the exact claim row (undelivered, same holder pid/start, ref still absent), and refuses unless the holder is neither this process nor alive and `_expired_observation` finds, for the same source and canonical key, only finite `first_seen` observations that are all past the late window (`_late_verdict == 'expired'`), including an exact-ref one, and every alias claim row for that session/state has an observed ref and a dead holder. Then it marks the row settled as `expired`, inserts the swept row in the same transaction and logs the drop after commit. Malformed refs, read errors, lock errors, young or unknown observations, a different scope for the same task id, and live holders all retain the claim; failures roll back.

## How it works

`tui_gateway/kanban_remote_expired_claims.py` (`settle_absent`, `_expired_observation`), `kanban_remote_notices.py` (`_settle_stale_claims`), `notice_fanout.py` (`_refresh_remote_chat`, `_prune_remote_absent`, `_remote_unchanged`, `_remote_projection`), `kanban_unloaded.py` (`SessionLookup`, `find_session_db`), `unloaded_diagnostics.py` (`remote_retry`, `maintenance_admitted`, `Stage`).

## Code

`tui_gateway/kanban_remote_expired_claims.py` (new), `kanban_remote_notices.py`, `notice_fanout.py`, `kanban_unloaded.py`, `unloaded_diagnostics.py`, `gateway/notification_retry_provenance.py`.

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. Test source, all under `tests/tui_gateway/` unless noted: `test_remote_absent_expired_claims.py` (T1-T9: clock and boundary, malformed/unknown observations, holders, alias vetoes, writer lock ordering and rollback, peer races, real persist/collect), `test_remote_change_absence.py` (A1-A5 absence ACK, unavailable retry, maintenance gating, snapshot change, cached health recheck, real finder open failures), `test_remote_change_absence_retry.py` (pending custody through refresh failure, exception, ACK failure, unavailable lookup, changed/removed snapshot), `test_remote_change_absence_warning.py` (warning once across maintenance rechecks; repeats for a new entry), `test_remote_change_review_regressions.py` (canonical source spelling, profile discovery failure then recovery, warning history pruning), `test_remote_change_retry_reasons.py`, `test_remote_change_diagnostics.py`, `test_card_change_stages.py`, plus touched `test_notice_fanout_durability.py`, `test_kanban_unloaded_busy.py` and `tests/hermes_cli/test_kanban_card_transition_outcome_refresh.py` (fixture-level edits only). The host-side sweeper acceptance mentioned in commit text is untested here; 6691 itself describes an unfinished checkpoint that later commits complete.

## Applying

Apply in ordinal order with 6596-6599 earlier in the series (shared diagnostics vocabulary).

## Gotchas

- Only *proven* absence is ACKed; discovery uncertainty keeps retry. A custom session lookup returning a bare tuple is treated as unavailable.
- Absence re-checks without a maintenance admission or a snapshot change are skipped, so a session that appears later is noticed at the next maintenance pass or snapshot change.
- Pending-recheck custody must outlive the absence-marker removal until the ACK is durable, but is in-memory only.
- `_remote_absent` and the warning set are per process.

## Why core, not a plugin?

TUI gateway notice fanout.
