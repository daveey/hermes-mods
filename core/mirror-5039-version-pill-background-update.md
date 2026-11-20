# Version pill: host-run core update progress

Mirror entries 5020–5026.

## Behavior

Desktop's version pill shows a core update the **host** is running (hermes-sync), not only updates Desktop started itself.

- `GET /api/hermes/update/progress` reads published `window.json`, `drain.json` and `governor-other-defers.json`. Missing, stale, foreign or malformed records are `null` (no progress), never an error.
- Elapsed times are computed on the host clock. The bar uses the drain-pass clock; the tooltip uses time since the update was due.
- Polling is passive: errors keep prior pill state; no-warm-backend misses log quietly.
- After a dropped update the version is refreshed. Hold-queue markers must not replace a live sibling backend's running-turn marker.

i18n keys for the background-update copy are regenerated (5023). 5025–5026 are test-only follow-ups (no cascade, real switch order, batched drop, switch waits for the answer).

## How it works

`hermes_cli/core_update_progress.py` maps the three records into one payload. Desktop `background-update.ts` / `store/updates.ts` poll and derive suffix, tooltip and bar. TUI `turn_marker.py` skips `update_hold_queue` markers as "not running" so a queued message waiting *for* the update does not look like a dead turn.

## Code

- `hermes_cli/core_update_progress.py`, `hermes_cli/web_routers/actions.py`
- `apps/desktop/src/lib/background-update.ts`, `apps/desktop/src/store/updates.ts`, `apps/desktop/src/api/system.ts`
- `tui_gateway/turn_marker.py`, `tui_gateway/session_auto_continue.py`
- `locales/_keys.desktop.json`, Desktop i18n overlay files

## Tests

Source tests (not executed here):

- `tests/hermes_cli/test_core_update_progress.py` (`test_draining_reports_elapsed_cap_and_what_it_waits_on`, `test_unusable_window_is_no_progress`, `test_route_serves_the_reader`, …)
- `tests/tui_gateway/test_hold_queue_marker_sibling.py`
- `apps/desktop/src/lib/background-update.test.ts`, `apps/desktop/src/store/updates-progress.test.ts`, `apps/desktop/src/api/system-update-progress.test.ts`, `apps/desktop/src/app/shell/hooks/use-statusbar-items.test.tsx`
- `apps/desktop/electron/api-expected-404.test.ts`

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply these mails on their own.

## Gotchas

- A `null` progress payload means "nothing to show", not a failed request.
- Do not treat an `update_hold_queue` marker as a running turn during drain.
