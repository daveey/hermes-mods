# Desktop notify receipts survive a backend crash

Patches: **3489**.

## Behavior
- A Desktop chat's background terminal job with notify enabled records a delivery obligation in its result receipt. If the Desktop backend dies uncleanly (kill, out-of-memory) after the job finished but before the chat got its notice, the next Desktop backend of the same profile re-delivers the notice once, with exit code and output.
- No duplicate after the notice was delivered: further restarts do nothing. A live writer backend keeps its own obligation.
- Non-notify jobs, CLI/worker-process jobs, other profiles' jobs and gateway-owned jobs do not wake a Desktop chat; a messaging-gateway start does not adopt a Desktop obligation. Gateway behavior is unchanged.
- The obligation is settled by wait, log read, kill, and by notifications being turned off, so those are not re-delivered. An orderly restart through the exit journal hands custody to the turn marker so the notice is not delivered twice.

## How it works
3489 reuses the existing obligation/receipt machinery. `tools/process_registry_obligations.py` has a Desktop-owned backend write the obligation in the same atomic receipt write, before the checkpoint entry is dropped, with an owner marker (`gateway` or `tui`; unmarked means gateway). Recovery re-arms only its own owner's obligations; `recover_from_checkpoint(owner="tui")` runs at startup and in the periodic checkpoint keeper, bounded by the existing age and recovery-count limits. The new `tui_gateway/completion_obligation.py` settles it on durable delivery, intended suppression, and journal custody transfer.

## Code
- `tools/process_registry_obligations.py`
- `tui_gateway/completion_obligation.py`
- `tui_gateway/exit_job_receipts.py`
- `tui_gateway/session_notifications.py`
- `tests/tui_gateway/test_tui_completion_obligation.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_tui_completion_obligation.py`: `test_unclean_backend_death_after_settle_delivers_the_notice_once`, `test_owed_notice_parks_while_the_writer_backend_lives`, `test_only_this_profiles_desktop_owed_notices_wake`, `test_consumed_or_quiet_completion_is_not_redelivered`, `test_orderly_exit_journal_takes_custody_and_delivers_once`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Defense in depth: the patch states no incident was shown to hit this window.
- A tui obligation written by the current process is never re-armed by itself.
