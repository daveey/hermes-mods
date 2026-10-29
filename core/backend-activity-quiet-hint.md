# Backend activity controls the running-turn quiet hint

Patch 0639 (`371e91ded4`).

## Behavior

A running turn with fresh backend `last_active` is not painted stuck merely because this window has received no events. Activity older than the quiet window sets the hint. A backend without that field leaves the renderer watchdog in charge.

## How it works

The existing 45-second silence probe asks `session.active_list`. Fresh runtime activity clears the hint and restarts the watchdog; old activity sets it, using the same quiet rule as rehydration. Backend-started or restart-resumed turns need not have a streaming pane in this window.

## Code

`apps/desktop/src/store/session-states.ts`.

## Tests

Added cases in `apps/desktop/src/store/session-watchdog.test.ts` cover backend activity and the silence/watchdog interaction. These are store-level regression tests; a live Electron multi-window drill is not part of this mirror verification.

## Applying

Apply 0639 after the session-state/watchdog patches with `git am -3`.

## Gotchas

Older backends retain the renderer-only fallback. A quiet hint is an activity signal, not proof of a failed turn.

## plugin?

No; core Desktop session state.
