# Session-state and Desktop wake fixes: passive notices, live-turn rows, change-watcher throttle, power settle

Patches:

- `patches/1100-fix-tui-out-of-band-adoption-counts-a-merge-carrier-.patch`
- `patches/1103-fix-desktop-hold-background-reads-while-asleep-and-s.patch`
- `patches/1104-refactor-desktop-move-extensionForMimeType-out-of-ma.patch`
- `patches/1105-perf-tui-throttle-change-watcher-session-digest-to-o.patch`
- `patches/1108-fix-sidebar-a-turn-running-outside-this-window-s-str.patch`

TUI gateway and Desktop fixes from the 1100-1108 range.

## Behavior

- 1100: out-of-band adoption counts a merge carrier's absorbed row ids as seen, so a woken turn no longer re-sends a passive notice.
- 1103: a `PowerSettleGate` in front of `hermes:api` fails background GETs fast between suspend and wake, waits a 15 s settle window plus 0-10 s jitter after resume, and never holds POSTs or foreground requests.
- 1104: `extensionForMimeType` moves from `main.ts` to `mime-extension.ts` (refactor, no behavior change).
- 1105: the change-watcher session digest runs at most once per 3 s on a busy state.db.
- 1108: session list/tree rows carry `turn_live: true` when the lineage holds a live durable turn lease, and the renderer folds it to 'working' so the outcome stripe yields to it.

## How it works

Renderer, main-process and TUI-gateway changes are independent; they share only the commit range.

## Code

`tui_gateway/prompt_turn.py`, `tui_gateway/change_watcher.py`, `tui_gateway/methods_projects.py`, `hermes_cli/turn_session_activity.py`, `hermes_cli/web_routers/profiles.py`, `hermes_cli/web_routers/sessions.py`, `apps/desktop/electron/power-settle.ts`, `mime-extension.ts`, `main.ts`, `apps/desktop/src/store/session-dot-state.ts`.

## Tests

`test_woken_turn_passive_notices_once.py` (1100), `power-settle.test.ts` (1103), `mime-extension.test.ts` (1104), `tests/tui_gateway/test_change_watcher.py` and `test_change_watcher_sessions.py` (1105), `tests/hermes_cli/test_turn_session_activity.py`, `tests/tui_gateway/test_projects_rpc.py`, `session-dot-state.test.ts` and `session-row.test.tsx` (1108). Desktop suites were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
