# Force update from the backend version pill

Mirror snapshot 5406; ordinals 5334, 5335, 5336, 5338, 5339.

Entries:

- **5334**: feat(update): Force update from the backend version pill
- **5335**: fix(update): Force update review findings
- **5336**: chore(i18n): regenerate desktop locale key index for Force update strings
- **5338**: fix(update): `hermes update --force` stops supervised gateways through the waiting stops
- **5339**: fix(update): review nits on the --force stop fix

## Behavior

- A user-typed turn that is not auto-continuable could hold the update drain open through its running tool call, so the apply stayed "not launchable". A right-click "Force update" on the backend version pill now writes a time-limited force request.
- **5334**: `core_update_force.py` writes `<root>/core-update/force-request` atomically (version, requested/expires times, requester); missing, garbage or expired reads as no request. The pause request can carry `force`; under it non-resumable turns' in-flight calls are cancelled after the normal mid-tool grace and the turn ends with a forced-stop reason, not continued. `GET/POST /api/hermes/update/force` report and write the request (409 when no update is owed). Desktop adds a status-bar context-menu item and copy.
- **5335**: a forced stop becomes a visible, retryable failure with its own error card; running-turn count covers every profile home and is null ("could not tell") when a state store is unreadable; the temp file is uuid-named; a second Force is ignored while one is pending.
- **5336**: regenerated desktop locale key index.
- **5338/5339**: `hermes update --force` over launchd or systemd gateways called a stale one-argument stop helper and never applied; it now uses the waiting stops (`_stop_unit`, `_stop_job`), raises `PauseRefused` on a failed bootout with a respawned gateway, and the PID wait checks birth time so a recycled PID is not mistaken for the gateway.

## How it works

- Core: `hermes_cli/core_update_force.py`, `admission_fence.py`, `web_routers/actions.py`, `update_cmd_force_stop.py`, `update_cmd_posix_pause.py`, `tui_gateway/update_pause*.py`, `prompt_turn.py`, `user_messages.py`, `agent/error_surface.py`.
- Desktop: status-bar item, force-update client, i18n catalogs.
- A request names no version; only the external update agent clears it.

## Code

Shipped non-test paths (union over these entries):

- `apps/desktop/electron/connection-config.ts`
- `apps/desktop/src/api/system.ts`
- `apps/desktop/src/app/shell/force-backend-update.ts`
- `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx`
- `apps/desktop/src/app/shell/statusbar-controls.tsx`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/en_force_update.ts`
- `apps/desktop/src/i18n/overlay-gaps.json`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/types_force_update.ts`
- `apps/desktop/src/types/hermes.ts`
- `hermes_cli/admission_fence.py`
- `hermes_cli/core_update_force.py`
- `hermes_cli/web_routers/actions.py`
- `tui_gateway/update_pause.py`
- `tui_gateway/update_pause_approval.py`
- `website/docs/user-guide/features/web-dashboard.md`
- `agent/error_surface.py`
- `apps/desktop/src/i18n/en_error_codes.ts`
- `apps/desktop/src/lib/error-surface.ts`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/user_messages.py`
- `locales/_keys.desktop.json`
- `hermes_cli/update_cmd_force_stop.py`
- `hermes_cli/update_cmd_posix_pause.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `apps/desktop/src/app/shell/force-backend-update.test.ts`
- `apps/desktop/src/app/shell/hooks/use-statusbar-items.test.tsx`
- `apps/desktop/src/app/shell/statusbar-context-menu.test.tsx`
- `tests/hermes_cli/serve_handler_ledger.json`
- `tests/hermes_cli/test_admission_fence_pause.py`
- `tests/hermes_cli/test_core_update_force.py`
- `tests/tui_gateway/test_update_pause_forced.py`
- `tests/tui_gateway/test_update_pause_forced_prompts.py`
- `apps/desktop/src/lib/error-surface-copy.test.ts`
- `tests/tui_gateway/test_update_pause_forced_visible.py`
- `tests/hermes_cli/test_update_force_stop.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The request file is a contract shared with the update agent (a plugin/external component); core only writes and reads it.
- Forced turns are *not* auto-continued; the user re-sends.
- The confirm copy does not promise a stopped-turn count because leases do not name their surface.

## Why core instead of a plugin

The pause, turn lease and admission state it overrides, plus the web route, live in core; only an update agent outside core consumes the request file.
