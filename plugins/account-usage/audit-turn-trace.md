# Turn tracing compatibility audit

Audited core `5971b39676e926097ab786beaae0b5cdfcb9504c` against the most recent
accepted `agent/turn_api_call.py` version, found by hashing file history:
`970106d8f54dbf693311b125eef6aa1a7cb69dbc` / SHA-256
`e18b1d3a6ce2f4fddb2dc91f404141f03f332a06db18835eb9745a2821949fe2`.
The new file digest is
`9924c03a03b01d103c9ca3701963950986331f718d2bbda9c2aa5a9b28ddad3b`.
All other execution-pinned files at the target core already match accepted hashes.

The entire delta imports `hermes_cli.web_turn_stages.mark` and calls
`mark('model_admitted')` in `_perform_api_call`, after class-request validation
and Codex transport preflight, before branching to streaming/non-streaming sends.
The helper observes a context-local trace, deduplicates stage names under its
trace lock, and emits bounded telemetry. It does not change the request, agent,
credentials, endpoint, model, retry state, or execution middleware. No new
credential/route fence is introduced or bypassed by request-local snapshots.

`perform_api_call` still encloses middleware dispatch in the same live frame;
selection's calling-agent frame lookup and code verification remain valid.
Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
`switch_model`, and `interruptible_*_api_call` are unchanged. Existing class-route
validation, transport preflight, update-pause and retry-deadline gates remain in
place and still execute around selected requests. `selection.py` and its
`recovery_wrapper` require no changes, so the fallback composition pin remains
unchanged. Keep all older accepted hashes for rolling deployment.
