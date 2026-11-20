# Auth: preflight stale relay receipts before pinned Anthropic requests

Patches: **4721, 4738**

- 4721 (`1c55764fc5`): fix(auth): preflight stale Anthropic relay receipts before pinned requests [daveey]
- 4738 (`86c3bb0bfd`): fix(auth): back off relay attempts without new receipts [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Before a pinned relay-credential request, a provider-scoped success receipt older than twice the pull cadence triggers one relay pull under the existing relay lock. Lock wait, command and async receipt wait share one 30 s budget and fail open; disable is rechecked after the wait.
- Failed attempts are coalesced across sessions; without new receipts, retry spacing persists per provider under the lock and doubles up to one hour, resetting when a newer receipt appears.

## How it works

`agent/credential_relay_preflight.py` implements the receipt check and backoff; `agent/credential_pool_pin.py` calls it. Other credential sources do not inspect receipts.

## Code

- `agent/credential_pool_pin.py`
- `agent/credential_relay_preflight.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/agent/test_relay_wake_preflight.py`: `test_first_request_after_gap_uses_applied_token`, `test_failed_pull_is_coalesced_across_sessions`, `test_fresh_or_future_receipt_never_pulls`, `test_unknown_receipt_does_not_start_pull`, `test_other_credentials_do_not_inspect_receipts`, `test_async_kickstart_waits_for_applied_receipt`, `test_async_receipt_timeout_fails_open_with_one_deadline`, `test_lock_wait_consumes_command_budget`, `test_busy_lock_times_out_without_command`, `test_peer_success_rechecked_after_lock`, `test_no_command_fails_open`, `test_failed_attempt_is_retried_after_cooldown`, `test_bad_attempt_history_does_not_prevent_catchup`, `test_expired_total_budget_never_starts_command` (+7 more)

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Fails open: a missing command or unknown receipt never blocks the request.
- Async kickstart success alone does not prove credentials were applied.
