# TUI gateway: failure reset time stays inside ErrorPayload

Patches: **1789** (`d5a41fe039`, `fix(tui_gateway): agent-build error events stop leaking failure_resets_at past ErrorPayload [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Agent-build error events map `failure_resets_at` to the declared `reset_at` instead of leaking an undeclared key that violated `ErrorPayload`.

## How it works

`tui_gateway/credential_failure.client_failure_fields()`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1789: a0cd0f3219 put the automation replay's clock (failure_resets_at) into session['agent_failure'], which is spread into the session-level 'error' event, into session_failure_payload's agent_init_failed enrichment and into the RPC error data. ErrorPayload/MessageCompletePayload forbid extra keys, so every quota-walled build logged a wire-contract ERROR (strict only in tests).

## Code

`tui_gateway/credential_failure.py`, `tui_gateway/session_registry.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tui_gateway/test_credential_failure.py`: `test_agent_init_failed_error_enrichment_honours_its_contract`, `test_quota_walled_build_error_event_honours_its_contract`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
