# One-shot gateway chat: stable retry identities and closed refused redials

Patches: **2930**.

## Behavior
- A one-shot gateway chat retried across an owner update reuses the same request id (create) or input id (submit) for the same run, but a new run gets fresh ids.
- A refused resume/redial closes its socket right away instead of holding it until the whole run exits.
- The admission notice does not claim an update is happening when none is.

## How it works
2930 wraps each redial in its own `AsyncExitStack` closed before the next dial in `gateway_chat`, and adjusts `gateway_chat_view` and `gateway_update_wait` for identity reuse and the notice wording.


## Code
- `hermes_cli/gateway_chat.py`
- `hermes_cli/gateway_chat_view.py`
- `hermes_cli/gateway_update_wait.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_gateway_chat_update_wait.py`: `test_admission_notice_does_not_claim_an_update`, `test_create_retry_reuses_request_id_but_a_new_run_does_not`, `test_submit_retry_reuses_input_id_but_a_new_run_does_not`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
