# Cron: hold a fire whose gateway dial failed

Patches: **3623, 3624**.

## Behavior
- A scheduled cron fire whose gateway WebSocket could not be opened (nothing submitted, no admission journal) is held and its slot stays due, instead of being booked FAILED.
- After 3 consecutive dial-failure holds the next one is booked FAILED, so a gateway that stays unreachable still alerts.
- A successful run, or a hold of another kind, resets the hold count.
- Manual fires (trigger / `hermes cron run`) whose dial failed are still booked FAILED, never held.
- Owner refusals, not-ready verdicts, local client refusals and any failure after submit are unchanged (transport loss after submit stays unknown, never held).

## How it works
- 3623 makes `run_canonical_job` raise `CronFireNotAdmitted('unreachable', 'connect_failed')` on a dial failure before submit. The hold count lives in `last_fire_error.connect_holds`, limit `CONNECT_HOLD_LIMIT` (3). `release_unadmitted_fire` moves from `cron/jobs.py` to `cron/unadmitted_fire.py`.
- 3624 requires `job['_scheduled_instant']` for the hold, because only scheduled fires are settled as held; manual fires previously crashed with no FAILED output doc.

## Code
- `cron/scheduler_authority.py`
- `cron/unadmitted_fire.py`
- `cron/jobs.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_cron_connect_failure_not_admitted.py` (file path as in the patch diffstat): `test_dial_failure_before_submit_holds_the_fire`, `test_run_canonical_job_raises_not_admitted_and_leaves_fire_error_unset`, `test_client_error_without_a_transport_cause_still_fails`, `test_transport_loss_after_submit_stays_unknown_never_held`, `test_dial_failure_past_the_hold_limit_is_booked_failed`, `test_a_successful_run_resets_the_hold_count`, `test_manual_run_dial_failure_is_booked_failed`, `test_another_hold_restarts_the_connect_hold_count`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The hold limit is a consecutive count; a persistently unreachable gateway delays the FAILED booking by up to 3 fires.
