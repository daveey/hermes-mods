# Core mods: changes to Hermes's own source

These are the changes I carry on my fork of
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). They can't be plugins
(or weren't, yet), so they ship here two ways:

- **`patches/`**: `git format-patch` output of every commit, numbered in apply order (`git am`-ready).
- **One doc per feature**: what it does, how it works, the code that matters, tests, how to apply it
  and the gotchas. It's written so your coding agent can re-implement the feature on a newer upstream
  when a patch no longer applies.

Keyboard shortcuts are summarized in [../shortcuts.md](../shortcuts.md).

## Features

| Doc | Patches | What you get |
|---|---|---|
| [session-navigation-shortcuts.md](session-navigation-shortcuts.md) | 0019 0021 0022 0026 | ⌘]/⌘[ next/prev session that needs you, ⌘⇧]/⌘⇧[ any session, ⌘⇧A archive-and-advance, ⌘R resume, ⇧⌘R reload |
| [ai-session-rename.md](ai-session-rename.md) | 0020 | ⌘⇧N rename, pre-filled with an AI short name (`session.title.suggest` RPC) |
| [project-palette.md](project-palette.md) | 0004 0018 0030 | ⌘⇧P moves a session to a project by typeahead; a new name offers "Create project" with an empty `~/code/<slug>` |
| [multi-gateway-projects.md](multi-gateway-projects.md) | 0023 0024 0025 0028 0029 | Sidebar shows every connected gateway's projects (colored, with show/hide toggles); move a session into any gateway's project; archive on the owning gateway |
| [session-takeover.md](session-takeover.md) | 0013 0014 0015 | "Take over here" for a chat that's live in another Hermes window; a hidden chat's agent keeps its Browser page |
| [auto-continue-resume.md](auto-continue-resume.md) | 0008 0010 | `desktop.auto_continue.resume_on_startup`: interrupted sessions resume when the backend boots, including turns cut off by a backend shutdown |
| [fork-updates-and-relaunch.md](fork-updates-and-relaunch.md) | 0007 0011 0012 0016 | **Start here if you'll carry patches.** `updates.fork_merge_upstream` lets `hermes update` merge upstream into your fork. Also: the app relaunches after a headless rebuild, `hermes serve --stop` spares Desktop's backend, and no "Switch to This device?" prompt |
| [macos-signed-python.md](macos-signed-python.md) | 0017 0027 | macOS: run Hermes from a Developer-ID-signed Python.app, so Full Disk Access, Accessibility and other permission grants survive Python upgrades |
| [small-fixes.md](small-fixes.md) | 0001 0002 0003 0005 0006 0009 | Completion sound "Off", sidebar chevron, no session preview cap, sleep isn't idleness, compression reload spares context engines, desktop-only plugin packages |

## Applying

The patches touch shared files (keybind registry, i18n, sidebar, the RPC contract), so they are
**not independent**. Apply them in order, either all of them or a prefix up to the last one you
need. Revert what you don't want afterwards.

```bash
cd ~/.hermes/hermes-agent              # the checkout `hermes` runs from (`hermes --version` shows it)
git switch -c mods                     # or work on your fork's main; see fork-updates-and-relaunch.md
git am -3 ~/code/hermes-mods/core/patches/*.patch
```

When I last tested (Oct 2026), all 30 applied on `upstream/main` except **0013**, which conflicts in
`tui_gateway/server.py`. [session-takeover.md](session-takeover.md) explains how to resolve it. After
resolving: `git add tui_gateway/server.py && git am --continue`.

Each doc's **Applying** section lists the smallest set of patches that applies and passes its tests,
for when you only want one feature.

If a later patch conflicts because upstream moved, re-implement it from its doc instead of forcing
the hunk. Each doc's **Code** and **Tests** sections are the spec.

## Building and running a patched Hermes

1. **Python changes** (`tui_gateway/`, `hermes_cli/`, `agent/`) take effect on the next process start:
   restart the gateway (`hermes gateway run`, or your launchd job) and the Desktop backend.
2. **Desktop changes** (`apps/desktop/`) need a rebuild:
   ```bash
   # macOS: sign it with your Developer ID, or the app is ad-hoc signed and macOS treats every
   # rebuild as a new app (permission grants reset, signature-checking launchers refuse it)
   APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
     CSC_IDENTITY_AUTO_DISCOVERY=true hermes desktop --build-only --force-build
   ```
   That takes about 3 minutes. Quit and reopen Hermes.app afterwards. You can also set
   `desktop.macos_signing_identity` once so every rebuild is signed.
3. **Tests.** Python: `scripts/run_tests.sh <files>` from the repo root. A bare `venv/bin/python -m pytest`
   can fail on a missing `ruamel`. Desktop: `cd apps/desktop && npx vitest run <file>`; vitest needs a
   current Node (v18 is too old). `npm run typecheck` checks types, and `npx eslint --fix <files>` fixes
   import/prop sort lint (0002 and 0022 trip it as committed). Run the tests each doc lists before you
   trust a rebased patch.

## Gotchas

- **`hermes update` drops uncommitted edits and resets to `origin`.** Keep the patches as commits on
  a fork you own, and set `updates.fork_merge_upstream: true` (patch 0007), so updates merge upstream
  into your commits. The first update after you adopt a fork still runs the *old* updater, which backs
  up and resets; that's fine once the fork contains upstream. Details are in
  [fork-updates-and-relaunch.md](fork-updates-and-relaunch.md).
- **`hermes update` stops a hand-started gateway or `hermes serve` and doesn't restart it.** Run
  anything you depend on as a supervised service (launchd `KeepAlive`).
- **Never run `hermes serve --stop` just to restart one server.** Without patch 0012 it also kills
  the Desktop app's own backend.
- **RPC contract.** Patches that add JSON-RPC methods or events also edit
  `apps/shared/src/gateway-contract.generated.ts` and `gateway-contract.openrpc.json`. If those
  conflict, regenerate them from the Python contracts (`tui_gateway/contracts/`) with
  `python scripts/gen_gateway_contracts.py`; don't hand-merge them.
- **i18n.** Every new UI string has to be translated in each `apps/desktop/src/i18n/*.ts` locale or
  listed in `overlay-gaps.json`, or `overlay-completeness.test.ts` fails. 0024's `sidebar.filter.*`
  keys trip this as committed; regenerate the list with `OVERLAY_GAPS_UPDATE=1`. After changing
  `en.ts`, run `npm run i18n:keys`.
- **Contract coverage.** 0020 adds the `session.title.suggest` RPC without a contract in
  `tui_gateway/contracts/`, so the contract-coverage test flags it. See
  [ai-session-rename.md](ai-session-rename.md).
- **Keybinds.** The defaults live in `apps/desktop/src/lib/keybinds/actions.ts` and the handlers in
  `apps/desktop/src/app/hooks/use-keybinds.ts`. `actions.test.ts` rejects chord conflicts.
- **Tags.** I tag my commits `[daveey]` in the subject so they're easy to list
  (`git log upstream/main..origin/main`). Use your own tag.
