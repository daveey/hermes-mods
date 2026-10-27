# Worker-launched Desktop test instances stay off the owner's screen

Patch **0459** (source `8e8da03e77`).

## Behavior

A Hermes Desktop started from inside a kanban worker or delegate child, as a test instance, runs with no Dock icon
and never shows or focuses a window. A test instance is unpackaged (`electron .`) or has its own
`--user-data-dir`. Agents still drive and screenshot it over CDP. The owner's packaged Desktop, reopened from a
worker (a worker's `hermes update`, mac-fixer), uses the default profile and stays visible.
`HERMES_DESKTOP_AGENT_VISIBLE=1` makes a test instance visible again.

Before this, card t_ed201037 (2026-10-27) reproduced a Desktop bug with its own `electron .`
(`--remote-debugging-port=9455 --user-data-dir=~/.hermes/cache/scratch/ssr-live/userdata`). It appeared as a
second Hermes app with its own Dock icon and window, so the owner saw "someone keeps launching the wrong Hermes".

## How it works

- `apps/desktop/electron/agent-test-instance.ts`: `isHiddenAgentTestInstance()` returns true when the keys
  `scrubLeakedWorkerEnv()` removed are non-empty, the instance is unpackaged or has a `--user-data-dir` flag, and
  the opt-in is unset. `hideAgentTestInstance()` sets the macOS `accessory` activation policy, hides the Dock
  icon, makes `app.focus` a no-op, and on `browser-window-created` replaces each window's `show`,
  `showInactive`, `focus` and `moveTop` with no-ops. Every Desktop window is created with `show: false`, so it
  still renders.
- `apps/desktop/electron/main.ts`: called right after the existing worker-env scrub, before `ready`. It logs
  `[hermes] worker-launched test instance: …`.

## Code

`apps/desktop/electron/agent-test-instance.ts` (new), `apps/desktop/electron/main.ts`.

## Tests

`apps/desktop/electron/agent-test-instance.test.ts`:
- hides an unpackaged `electron .` a worker started; hides a packaged build on a scratch profile; keeps the
  owner's packaged Desktop visible when a worker reopens it; never hides a Desktop no worker launched; the
  opt-in shows a test instance.
- `hideAgentTestInstance`: on macOS it sets `accessory`, hides the Dock icon, and no window can be shown,
  focused or raised; off macOS the Dock is untouched and windows still never show.

Manual launch on macOS (2026-10-27, not automated): the built main bundle, launched with a worker env and a
scratch `--user-data-dir`, reported `ApplicationType=UIElement` (no Dock icon) and served a CDP page target. With
`HERMES_DESKTOP_AGENT_VISIBLE=1` it reported `Foreground`. The on-screen window count was 0 in both runs, so the
manual run does not show the window suppression; that part rests on the unit tests.

## Applying

Independent of the other patches. It needs `scrubLeakedWorkerEnv()` (`electron/leaked-worker-env.ts`); if that
moves, call the two functions wherever the inherited worker env is detected, before any window is created.

## Gotchas

- A worker that really needs to watch the instance (a human-in-the-loop demo) must set
  `HERMES_DESKTOP_AGENT_VISIBLE=1`; otherwise `show()` silently does nothing.
- Only instances launched with a worker marker in the env are affected. A probe started from a plain shell is
  visible, as before.
- The skill `inspecting-hermes-desktop-dom` (private) tells workers to keep probes hidden and to run them from a
  `hermes-core-wt` worktree.

## Plugin?

No. It must act before the first window exists, in the Electron main process.
