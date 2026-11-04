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
