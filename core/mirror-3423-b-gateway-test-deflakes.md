# Gateway test deflakes

Patches: **3010, 3013**.

## Behavior
- Test-only changes; no runtime behavior changes. The API admission off-loop writer test uses an event handshake and thread identity instead of wall-clock heartbeat gaps, and the gateway chat-update-wait admission expiry test uses a virtual clock, asserting retries before and refusal at the production deadline.

## How it works
- 3010 replaces timer-based holding with a trace-callback handshake.
- 3013 replaces a 50 ms wall-clock window with a module-local virtual clock.

## Code
- `tests/gateway/test_api_admission_off_loop.py`
- `tests/gateway/test_gateway_chat_update_wait.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

No test files in these patches.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
