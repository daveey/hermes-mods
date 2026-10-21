# Browser tabs open in the background (no focus theft)

Patches:

- `patches/0163-fix-browser-open-the-own-tab-preamble-s-tab-in-the-b.patch`: the `browser_exec` own-tab preamble creates its tab with `background=True`.
- `patches/0164-fix-browser-CDP-supervisor-opens-its-fallback-page-i.patch`: the CDP supervisor's fallback page is created with `background=True`.

What you see: starting a `browser_exec` session (or a supervisor attaching to a browser that has no page) no longer pulls the agent Chrome in front of the app you are typing in.

## Behavior

- Before: a foreground `Target.createTarget` made macOS give Chrome keyboard focus, so every new `browser_exec` session (0163) and every supervisor attaching to a page-less browser (0164) stole focus from the user's app.
- Now: `_OWN_TAB_PREAMBLE` in `tools/browser_use_cli.py` calls `Target.createTarget(url='about:blank', background=True)` and the supervisor's `_attach_initial_page` passes `{"url": "about:blank", "background": True}`. The harness's own `new_tab()` already did this.
- The tab is still created fresh (not reusing a blank current tab), because a sibling daemon may hold that one.

## How it works

- `tools/browser_use_cli.py`: the preamble's `_hermes_ensure_own_tab()` makes a fresh target and `switch_tab()`s to it; only the `createTarget` call changed.
- `tools/browser_supervisor.py`: `CDPSupervisor._attach_initial_page` creates `about:blank` only when `Target.getTargets` lists no page.

## Code

`tools/browser_use_cli.py` (the one `createTarget` call in the preamble), `tools/browser_supervisor.py`.

## Tests

- `tests/tools/test_browser_use_cli.py::TestOwnTabPreamble::test_preamble_opens_its_tab_in_the_background`: AST check that every `createTarget` call in the preamble passes `background=True`. Failed before 0163; the whole file passes (106).
- `tests/tools/test_browser_supervisor_background_tab.py::test_initial_page_created_in_background`: with a faked `_cdp`, the single `Target.createTarget` is `{"url": "about:blank", "background": True}`. Failed before 0164.
- Not tested: that macOS really keeps focus (needs a live Chrome).

## Applying

Standalone; the two patches are independent of each other and of the rest of the series.

## Gotchas

- Other CDP clients that create a foreground target still steal focus. The `invisible-chrome-agent` skill's supervisor focus guard (hermes-mods `4b6d375`) is the backstop that hands focus back.
- Already-running sessions keep the old preamble until restarted.

## Plugin?

No. Both calls are inside core tool code (a string preamble and the supervisor); no plugin hook intercepts them.
