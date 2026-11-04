# ⌘B park and outcome refresh write on the live owner

Patches: **1793** (`774d5e7198`, `fix(sessions): ⌘B park and outcome refresh write on the live owner, not under the offline lock [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- `POST /api/sessions/{id}/mute` and `GET /outcome?refresh=1` go through the live owner's store instead of `exclusive_maintenance`, which a running gateway always holds, so ⌘B no longer fails with "Exclusive maintenance refused".

## How it works

`hermes_cli/web_routers/sessions.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1793: POST /api/sessions/{id}/mute and GET /outcome?refresh=1 took exclusive_maintenance, which a running gateway always holds, so ⌘B on a gateway-served profile toasted 'Could not update the parked state: Exclusive maintenance refused' and rolled back (hub: 33 refusals since 2026-10-30). Route them through the live owner's own store like owner-backfill. Live-gateway test (RED = 409), plus a hotkey test for ⌘B from the page and from the composer.

## Code

`hermes_cli/web_routers/sessions.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/src/app/hooks/use-keybinds-toggle-blocked.test.tsx`: `fires from the page body`, `fires while typing in the composer`
- `tests/gateway/test_mute_live_owner.py`: `test_mute_and_outcome_refresh_run_on_the_live_owner`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.


## Follow-up 1888: SSH-isolated backends forward ⌘B park to the live owner

Patches: **1888** (`20b09e5165`, `[daveey] fix(sessions): ⌘B park from an SSH-isolated backend forwards to the live gateway owner`). Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

### Behavior

- An SSH-isolated `serve` backend with no gateway runner replays `POST /api/sessions/{id}/mute` (and the related outcome writes) on the live owner's HTTP API instead of taking the offline maintenance lock and returning 409 (1888).

### How it works

`hermes_cli/web_routers/sessions.py` forwards to the owner when no runner is composed into the app.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1888: 774d5e7198d moved POST /api/sessions/{id}/mute, GET /outcome?refresh=1 and owner-backfill onto the live owner's store, but only when the gateway runner is composed into the same app. Desktop on a remote host (hermes-hub) talks to an SSH-isolated `serve` with no runner, which still took the offline maintenance lock and 409'd ('Exclusive maintenance refused'; hub gui.log at 05:35 and 09:40 after that fix deployed). Such a backend now replays the write on the owner's own HTTP API with a single-use native-http ticket minted on its control socket. Live-gateway test with an uncomposed app (RED =...

### Code

`hermes_cli/web_routers/sessions.py`.

### Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_mute_live_owner.py`: `test_mute_and_outcome_refresh_run_on_the_live_owner`, `test_uncomposed_backend_forwards_mute_to_the_live_owner`, `test_owner_refusing_the_forward_answers_503_not_a_credential_rejection`, `test_no_gateway_keeps_the_offline_write`, `test_standalone_profile_ticket_is_minted_on_the_profiles_own_socket`, `test_unreachable_owner_answers_503_with_discoverys_reason_not_a_bare_409`, `test_multiplexer_served_profile_keeps_its_control_home_and_forwards_the_profile`, `test_named_profile_backend_names_its_profile_to_the_multiplexer`
- `tests/hermes_cli/test_session_maintenance_http.py`
