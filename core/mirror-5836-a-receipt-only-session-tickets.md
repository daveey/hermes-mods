# Receipt-only native session tickets (mirror 5500, 5501)

## Behavior
- A prompt-receipt poller can request a ticket with an explicit prompt-receipt purpose carrying only `session:receipt`, instead of an interactive ticket that grants control and operator privileges.
- A socket whose actor holds `session:receipt` refuses every RPC except `prompt.receipt` with `permission_denied` (code 4001), including plugin/ancillary RPCs. `receipt()` authorizes `session:receipt` instead of `session:submit` only when called receipt-only, and in that mode the same-owner check cannot be bypassed by `session:control`. Write callers keep `session:submit`. Receipt-only sockets are not admitted during draining (only `worker-adoption` is), and the docs say receipt-only tickets cannot use host scope.
- The native ticket redemption loop in `run_api.py` now tries `interactive`, `worker-adoption`, then `prompt-receipt`; a ticket matching none falls through to the browser/OAuth path as before. `operator` is true only for `interactive`. 5501 updates `test_webapp_ws_originless_remote_ticket.py` (the diff shows no named function change). The developer docs add that an older gateway rejects the purpose and callers needing least privilege must not silently retry as `interactive`; this is attenuation for the ticket holder, not isolation from the same OS user.

## How it works
`gateway/session_authority.py` defines the purpose/scope, `gateway/session_controls.py` and `run_api.py` gate the socket, `runtime_bootstrap.py` registers it. Docs: `website/docs/developer-guide/gateway-internals.md`.

## Code
`gateway/run_api.py`, `gateway/runtime_bootstrap.py`, `gateway/session_authority.py`, `gateway/session_controls.py`.

## Tests
Source: `tests/gateway/test_prompt_receipt_ticket.py` (by test names: a receipt ticket cannot read a foreign local owner's receipt (parametrized `foreign`); ticket binding and single use; it reads its own admission but no other RPC; an interactive owner (parametrized by source) keeps receipts and controls), `test_runtime_bootstrap.py` (private control peer mints a profile-bound ticket, parametrized by purpose), `test_session_socket_preload.py` (a drain that began while the handler loaded refuses the attach, parametrized by purpose), and `test_webapp_ws_originless_remote_ticket.py` (touched, no new test names). No dedicated direct-cancel test is named in the diff. Not executed in this mirror pass.

## Applying
Apply both; 5501 updates an exact-sequence assertion broken by 5500.

## Gotchas
Clients must request `"purpose": "prompt-receipt"` explicitly, and machine pollers should send `"restart_wait": "refuse"` per the docs.

## plugin?
No.
