# Hosted-room test: plant the old cache with the driver stopped

Patches: **3707**.

## Behavior
- Test-only fix for a flaky hosted-room test: the pre-migration room cache is now planted while the hosted-room driver is stopped, then the driver is restarted, matching how an upgraded build meets that cache at boot.
- No production behavior change.

## How it works
3707 changes `test_same_thread_followup_migrates_and_delivers_committed_peer_reply`. Previously it wiped the policy transcript and its schema-state row while the driver was live; a final checkpoint sync that passed its schema check before the wipe could index a later message over the emptied transcript, and nothing rebuilt it because the wipe leaves the cursor alone (a real migration resets the cursor). Stopping the driver first removes the race.

## Code
- `tests/tui_gateway/test_hosted_room_service.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_hosted_room_service.py`: `test_same_thread_followup_migrates_and_delivers_committed_peer_reply` (modified).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
