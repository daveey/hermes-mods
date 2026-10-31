# Kanban: a run lost to its owner's unclean death is `owner_restart_lost`; systemic crashes get a fix card

Patches: 1340 1341 1342

- `patches/1340-fix-kanban-book-a-run-lost-to-its-owner-s-unclean-de.patch`
- `patches/1341-refactor-kanban-move-_account_crashes-into-kanban_db.patch`
- `patches/1342-test-kanban-recovery-probe-expects-an-owner-SIGKILL-.patch`

## Behavior

- **Owner death is not the card's fault.** A gateway crash made its in-flight managed runs exit 1 with `service_identity_unverified`; three shared the fingerprint and the systemic rule parked them in sticky triage with no owner. A clean owner restart already booked such runs `owner_restart_lost` (uncharged); an unclean death now does the same.
- **Rule.** A crashed run is rebooked `owner_restart_lost` (`infrastructure: true`, no failure counted, respawn spaced by `check_respawn_guard`, event carries `owner_death_pid` and `owner_exit` = `unclean` or `pid_changed`) when (a) the worker reports its owner gone (`service_identity_unverified`, `owner admission settled interrupted`, or a `managed worker lost:` note naming owner/gateway/shutdown/restart) and (b) the owner pid in the run's `owner_admitted` receipt has an UNCLEAN record in `logs/gateway-exit-diag.log` or is dead/recycled. A live owner means an ordinary crash.
- **Crash-loop guard.** A card also in flight at the owner's previous unclean death is charged normally (`owner_death_repeat`) and never counted toward the systemic rule.
- **Systemic crashes get an owner.** A non-owner fingerprint seen >= 3 times in one sweep opens one fix card per fingerprint (idempotency key `systemic-crash:<hash>`, reused across ticks; a done fix card whose crash returns opens `:r2`...). Tripped cards are linked as its children, carry `fix_card` in `gave_up`, and resume when it is done. If the fix card cannot be opened the old sticky hold applies; if only the link fails the card gets `gave_up` with `fix_card_link_failed` + sticky. The fix card itself dying of the crash is held for an operator.
- 1341 moves `_account_crashes` into `kanban_db_dispatch_crash_accounting.py` (file-size cap); no behavior change.
- 1342 updates the gateway recovery probe: an owner SIGKILL mid-turn now ends with `consecutive_failures == 0`, run outcome `reclaimed`, exactly one `owner_restart_lost` event with `infrastructure` true.

## Code

`hermes_cli/kanban_owner_death.py`, `kanban_systemic_crash.py`, `kanban_db_dispatch.py` (hook in `_reclaim_dead_workers`), `kanban_db_dispatch_crash_accounting.py`; probe `tests/gateway/fixtures/kanban_recovery_probe.py`.

## Tests

`tests/hermes_cli/test_kanban_owner_death.py`: `test_runs_lost_to_an_unclean_owner_death_requeue_uncounted`, `test_owner_pid_gone_without_a_ledger_record_requeues_too`, `test_a_worker_lost_note_counts_only_when_it_names_the_owner_going_away` (parametrized), `test_owner_still_alive_is_not_an_owner_death`, `test_a_card_in_flight_at_two_consecutive_unclean_deaths_is_charged`, `test_a_systemic_crash_opens_one_fix_card_the_tripped_cards_wait_on`. The 1342 probe assertions are in a gateway fixture; not run for this mirror. Untested: fix-card key rounds (`:rN`), link-failure fallback, fix-card-dies-of-it hold, and the 1341 move beyond the existing suite.

## Applying

Apply 1340, 1341, 1342 in order. No config.

## Gotchas

- The ledger is read from the tail (256 KiB) of the owner's `gateway-exit-diag.log`; an unreadable ledger falls back to the pid-gone check.
- Tripped cards under a fix card must not be re-queued by hand.
