# Small fixes

Six small, self-contained patches to Hermes core. Each one applies on its own; none depends on another patch in
`patches/`. Paths below are relative to the hermes-agent repo root.

| Patch | Area | Change |
|-------|------|--------|
| [0001](patches/0001-feat-desktop-add-Off-option-to-completion-sound-sett.patch) | Desktop | Completion sound gets an "Off" option |
| [0002](patches/0002-fix-desktop-chevron-on-New-session-row-collapses-the.patch) | Desktop | A chevron on the "New session" row collapses the sidebar nav |
| [0003](patches/0003-fix-desktop-show-all-sessions-by-default-no-preview-.patch) | Desktop | The sidebar shows all sessions by default, with no preview cap |
| [0005](patches/0005-fix-tui-gateway-host-sleep-doesn-t-count-as-turn-idl.patch) | TUI gateway | Host sleep doesn't count as turn idleness for the WS-orphan reaper |
| [0006](patches/0006-fix-tui-gateway-live-compression-config-reload-leave.patch) | TUI gateway | Live compression config reload leaves context-engine plugins alone |
| [0009](patches/0009-fix-plugins-desktop-only-packages-load-without-Pytho.patch) | Plugin loader | Desktop-only packages load without Python; `provides_middleware` is a known manifest field |

How to run tests (used by every section below):

- Python: `scripts/run_tests.sh <test-file>` from the repo root. It bootstraps the managed runtime. A bare
  `venv/bin/python -m pytest` can fail at import (`No module named 'ruamel'`) when that venv isn't the
  activated runtime.
- Desktop: `cd apps/desktop && npx vitest run --project ui <test-file>`. Typecheck: `npx tsc -p . --noEmit`.
  Lint: `npx eslint <files>`.

---

## 0001: "Off" option for the completion sound

**Behavior.** Settings → Notifications → Completion sound used to offer 14 presets and no way to turn the cue off.
The only way to silence it was the global haptics mute, which also mutes other cues. Now the dropdown has an
`Off` item at the top. With Off selected, nothing plays on `message.complete` and the Preview button is silent.
The value is stored as `0` under the existing localStorage key `hermes.desktop.completionSoundVariantId`, so
there's no migration. Values outside 0–14 still fall back to the default preset (1).

**Code.** `apps/desktop/src/store/completion-sound.ts`:

```ts
export const COMPLETION_SOUND_OFF_ID = 0

export function isCompletionSoundOff(variantId: number): boolean {
  return variantId === COMPLETION_SOUND_OFF_ID
}

export function resolveCompletionSoundVariantId(variantId: number): number {
  if (variantId === COMPLETION_SOUND_OFF_ID) {
    return COMPLETION_SOUND_OFF_ID
  }
  return Number.isInteger(variantId) && variantId >= 1 && variantId <= VARIANT_COUNT
    ? variantId
    : DEFAULT_COMPLETION_SOUND_VARIANT_ID
}
```

`apps/desktop/src/lib/completion-sound.ts`. The deferred `play()` re-checks the setting, so switching to Off while
a deduped play is pending still silences it:

```ts
export function previewCompletionSound(variantId?: number) {
  const resolved = resolveCompletionSoundVariantId(variantId ?? $completionSoundVariantId.get())
  if (resolved === COMPLETION_SOUND_OFF_ID) {
    return
  }
  playVariant(resolved)
}

export function playCompletionSound(dedupeKey?: string) {
  // ...existing ambient/mute early return...
  if (isCompletionSoundOff($completionSoundVariantId.get())) {
    return
  }
  const play = () => {
    if (isCompletionSoundOff($completionSoundVariantId.get())) {
      return
    }
    playVariant($completionSoundVariantId.get())
  }
  // ...existing dedupe scheduling of play()...
}
```

`apps/desktop/src/app/settings/notifications-settings.tsx`, first child of `<SelectContent>`:

```tsx
<SelectItem value={String(COMPLETION_SOUND_OFF_ID)}>Off</SelectItem>
```

**Tests.** None added. Check it with typecheck and lint, then by hand: pick Off, press Preview, and finish a turn.

**Gotcha.** The `Off` label is hard-coded English, not an i18n key. Upstream's preset names (`variant.name`) are
untranslated too, so this matches the dropdown. Keep the store free of imports from `lib/completion-sound.ts`,
because the lib imports the store's atom (that's why the store validates by range).

---

## 0002: chevron on the "New session" row collapses the sidebar nav

**Behavior.** The nav block at the top of the sidebar (New session plus the other nav items) takes vertical space
from the session list, and upstream has no way to hide it. Now a small chevron sits on the New session row, before
its shortcut hint. It shows `chevron-up` when the block is expanded and `chevron-down` when collapsed. Clicking it
hides or shows every nav item except New session. The state persists in localStorage as `sidebar.nav.collapsed`
(`'1'`/`'0'`, default expanded). Clicking the chevron doesn't start a new session and doesn't start a drag.

**Code.** All in `apps/desktop/src/app/chat/sidebar/index.tsx`, inside `ChatSidebar`:

```tsx
const [navCollapsed, setNavCollapsed] = useState(() => {
  try {
    return localStorage.getItem('sidebar.nav.collapsed') === '1'
  } catch {
    return false
  }
})
const toggleNavCollapsed = useCallback(() => {
  setNavCollapsed(prev => {
    const next = !prev
    try {
      localStorage.setItem('sidebar.nav.collapsed', next ? '1' : '0')
    } catch {
      // ignore quota / disabled storage
    }
    return next
  })
}, [])

// nav render:
{(navCollapsed ? navItems.filter(item => item.id === 'new-session') : navItems).map(item => { /* ... */ })}

// inside the new-session SidebarMenuButton, replacing the bare KbdGroup:
{isNewSession && (
  <>
    <button
      aria-label={navCollapsed ? 'Expand nav' : 'Collapse nav'}
      aria-expanded={!navCollapsed}
      className="ml-auto flex size-5 items-center justify-center rounded text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground [-webkit-app-region:no-drag]"
      onClick={event => {
        event.stopPropagation()
        event.preventDefault()
        toggleNavCollapsed()
      }}
      onPointerDown={event => {
        // Keep the new-session drag source from engaging on the chevron.
        event.stopPropagation()
      }}
      type="button"
    >
      <Codicon name={navCollapsed ? 'chevron-down' : 'chevron-up'} className="size-3.5" />
    </button>
    {/* ml-auto moves from the KbdGroup to the button */}
    <KbdGroup className={cn('opacity-55', newSessionKbdFlash && 'opacity-100!')} keys={newSessionKbd} size="sm" />
  </>
)}
```

**Tests.** None added. The existing sidebar suites still pass with the patch applied:
`npx vitest run --project ui src/app/chat/sidebar/chat-sidebar.integration.test.tsx
src/app/chat/sidebar/sessions-section-new-session-drag.test.tsx`.

**Gotcha.** The patch as committed fails `eslint` with two `perfectionist/sort-jsx-props` errors: `aria-expanded`
must come before `aria-label`, and `className` before `name` on the `Codicon`. It also raises five
`padding-line-between-statements` warnings. Patch 0020 reformats the same file and clears them, so the
full series lints clean. If you take 0002 alone, run `npx eslint --fix src/app/chat/sidebar/index.tsx`. The aria labels are hard-coded English. The `[-webkit-app-region:no-drag]` class matters because
the button sits inside the titlebar drag region; without it, clicks drag the window.

---

## 0003: show all sessions by default, no preview cap

**Behavior.** Project and profile groups in the sidebar used to show a short preview (`PROJECT_PREVIEW_COUNT`
rows) with a "…" affordance. Profile groups ignored the "Show all sessions" toggle and were always capped. Now:

- `$sidebarShowAllSessions` (localStorage `hermes.desktop.sidebarShowAllSessions`) defaults to `true`.
- With the toggle on, every group, including profile groups, shows every loaded session.
- With it off, profile groups fall back to the preview slice and workspace groups to their paged lane.
- "Reset view" sets the toggle back to `true`. Turning it *off* now counts as a customization, which is what
  makes "Reset view" appear.

A stored `false` from an earlier explicit choice is still respected.

**Code.** `apps/desktop/src/store/layout.ts`:

```ts
export const $sidebarShowAllSessions = persistentAtom(SIDEBAR_SHOW_ALL_SESSIONS_STORAGE_KEY, true, Codecs.bool)

// in $sidebarViewCustomized's computed():
    cardRows ||
    !showAllSessions ||   // was: showAllSessions ||
    filtersActive

// in resetSidebarView():
  $sidebarShowAllSessions.set(true)   // was: false
```

`apps/desktop/src/app/chat/sidebar/projects/workspace-group.tsx`. `showAllSessions` is checked first:

```ts
const visibleSessions = showAllSessions
  ? sessions
  : isProfileGroup
    ? sessions.slice(0, PROJECT_PREVIEW_COUNT)
    : lane.shown
```

**Tests.** Two existing tests change:

- `src/store/layout-sidebar-view.test.ts` flips the expectations: the default is `true`, setting it to `false`
  counts as customized, and reset stores `'true'`.
- `src/app/chat/sidebar/projects/overview-row.test.tsx` adds `beforeEach(() => $sidebarShowAllSessions.set(false))`,
  because those tests cover the capped mode.

Run: `npx vitest run --project ui src/store/layout-sidebar-view.test.ts
src/app/chat/sidebar/projects/overview-row.test.tsx` (13 tests pass).

**Gotcha.** If you re-implement this, flip all three places in `layout.ts` (the default, the customized check, and
the reset). Flipping only the default makes "Reset view" show up for every user who never touched the setting.

---

## 0005: host sleep doesn't count as turn idleness for the WS-orphan reaper

**Behavior.** When the Desktop's WebSocket drops, the gateway's WS-orphan reaper checks whether the running turn's
activity is fresh, against `_WS_ORPHAN_ACTIVITY_STALE_S` (default 600 s; env
`HERMES_TUI_WS_ORPHAN_ACTIVITY_STALE_S` or config `ws_orphan_activity_stale_s`). Upstream measures freshness with
the wall clock (`seconds_since_activity`, from `time.time()`). If you close the lid mid-turn and open it more than
10 minutes later, a healthy turn looks stale the moment the machine wakes, and the reaper interrupts it before the
Desktop can reattach. With this patch, freshness uses awake time (`time.monotonic()`), which doesn't advance while
the host sleeps on macOS or Linux. An agent that's genuinely stuck while the host is awake is still reaped after
the same threshold.

**Code.** `agent/activity_tracking.py`, in `ActivityTrackingMixin._touch_activity()` next to the wall-clock stamp:

```python
            self._last_activity_ts = time.time()
            # Awake-time stamp: time.monotonic() stops while the host sleeps (macOS), so readers that must not
            # count a closed lid as idleness (the WS-orphan reaper) measure against this, not the wall clock.
            self._last_activity_mono = time.monotonic()
```

`tui_gateway/session_lifecycle.py`, `_ws_orphan_turn_activity_is_fresh()`. The new branch goes after the
compute-host branch (unchanged) and before the `get_activity_summary()` fallback:

```python
    agent = session.get("agent")
    mono = getattr(agent, "_last_activity_mono", None)
    if isinstance(mono, (int, float)) and not isinstance(mono, bool):
        return 0 <= time.monotonic() - float(mono) < _WS_ORPHAN_ACTIVITY_STALE_S
    if not callable(summary_fn := getattr(agent, "get_activity_summary", None)):
        return False
    # ...unchanged wall-clock summary path...
```

**Tests.** New file `tests/tui_gateway/test_ws_orphan_sleep_activity.py` (4 tests):

- 635 s of wall-clock gap with 5 s of awake time stays fresh
- 601 s of awake idleness is stale
- an agent without `_last_activity_mono` falls back to the summary
- `_touch_activity` sets the stamp

Run: `scripts/run_tests.sh tests/tui_gateway/test_ws_orphan_sleep_activity.py`.

**Gotcha.** The tests import `tui_gateway.server` and monkeypatch `server._WS_ORPHAN_ACTIVITY_STALE_S` and
`server.time.monotonic`. That works because `session_lifecycle` functions resolve those names through the server
module's namespace. A re-implementation that moves the constant elsewhere has to update the tests. Once the stamp
exists, the summary path is never consulted for that agent, so anything else that refreshes only
`_last_activity_ts` won't count as activity for the reaper.

---

## 0006: live compression config reload leaves context-engine plugins alone

**Behavior.** On every turn, `_apply_live_compression_config()` re-applies `compression.*` from `config.yaml` to
`agent.context_compressor`. When that object is a context-engine plugin (for example LCM) rather than the
built-in `ContextCompressor`, upstream overwrote the plugin's own `threshold_percent` and `tail_mode`. It then
called built-in private methods (`_coerce_threshold_tokens_cap`, `_effective_threshold_percent`) that the plugin
doesn't have. The resulting `AttributeError` was swallowed and logged on every turn. With this patch, plugins keep
their own thresholds and the log noise is gone. These agent-level keys still hot-reload:

- `compression.enabled`
- `codex_responses_native`
- `codex_responses_compact_threshold`
- `idle_compact_after_seconds`
- the `model.context_length` pin

Behavior with the built-in compressor is unchanged.

**Code.** `tui_gateway/session_compression.py`, in `_apply_live_compression_config()`, right after the
`cc is None` return:

```python
    from agent.context_compressor import ContextCompressor
    if not isinstance(cc, ContextCompressor):
        # A context-engine plugin (e.g. LCM) derives its own thresholds from its own config. Writing the
        # built-in compressor's privates onto it clobbered the plugin's threshold_percent and then raised
        # on ``_coerce_threshold_tokens_cap`` every turn. Only the agent-level context-length pin applies.
        new_ctx = config_context_length_for_runtime(agent, cfg)
        if new_ctx is not None:
            agent._config_context_length = new_ctx
        return
```

(`config_context_length_for_runtime` is already imported at the top of that function from `agent.agent_init`.)

**Tests.** `tests/tui_gateway/test_compression_config_hot_reload.py` gains
`test_plugin_context_engine_is_not_rewritten_by_live_sync`. It uses a `_PluginEngine` with `threshold_percent`,
`tail_mode`, and `context_length` but none of the built-in privates. The test checks three things: the sync
doesn't raise, `agent.compression_idle_compact_after_seconds` picks up `600`, and the plugin's `threshold_percent`
and `tail_mode` are untouched. Run: `scripts/run_tests.sh tests/tui_gateway/test_compression_config_hot_reload.py`
(15 tests pass).

**Gotcha.** The plugin branch writes only `agent._config_context_length`. The built-in path calls
`set_config_context_length()` and also updates `cc.context_length`. So for a plugin engine, the new pin isn't
copied onto the engine, and removing `model.context_length` doesn't clear the agent's pin until the session
restarts. That's deliberate (the engine owns its window), but it's a behavior difference.

---

## 0009: desktop-only plugin packages load without Python; `provides_middleware` is a known manifest field

**Behavior.**

1. A plugin package that ships only a Desktop half (`plugin.yaml` + `desktop/plugin.js`, no `__init__.py`) used to
   fail every gateway start with `No __init__.py`, even though Electron loads its JS without any Python. Now it
   loads enabled with no error. A package with neither `__init__.py` nor `desktop/plugin.js` (and no
   `provides_locales`) still fails as before.
2. A v2 manifest that declares `provides_middleware` (it uses `ctx.register_middleware`, the same way
   `provides_tools` and `provides_hooks` declare their registrations) no longer logs an "unknown manifest field"
   warning.

**Code.** `hermes_cli/plugins_loader.py`, `PluginLoaderMixin._is_manifest_only_language_pack()`. Its callers in
`_import_and_register()` already skip both the plugin-host load and the directory import when it returns true:

```python
    @staticmethod
    def _is_manifest_only_language_pack(manifest: PluginManifest) -> bool:
        """A plugin with no ``__init__.py`` that is complete without Python: a ``provides_locales`` pack
        (loads from ``locales/``) or a desktop-only package (Electron loads ``desktop/plugin.js``)."""
        if not manifest.path or (Path(manifest.path) / "__init__.py").is_file():
            return False
        return bool(manifest.provides_locales) or (Path(manifest.path) / "desktop" / "plugin.js").is_file()
```

`hermes_cli/plugins_manifest.py`:

```python
_KNOWN_MANIFEST_FIELDS: set[str] = {
    "name", "version", "description", "author", "requires_env", "provides_tools", "provides_hooks",
    "provides_middleware",
    # ...rest unchanged...
```

**Tests.** New file `tests/hermes_cli/test_plugins_desktop_only.py` (3 tests). Each builds a plugin under
`$HERMES_HOME/plugins/panel_only`, enables it in `config.yaml`, and runs `PluginManager().discover_and_load()`.
The tests cover:

- desktop-only loads with `error is None`, `enabled`, and no `No __init__.py` log
- no Python and no desktop half still errors with `__init__.py`
- `provides_middleware: []` raises no unknown-field warning

Run: `scripts/run_tests.sh tests/hermes_cli/test_plugins_desktop_only.py`.

**Gotcha.** The method name still says "language pack" though it now covers desktop-only packages too; keep the
name if you want the patch to stay small. The check is on the `desktop/plugin.js` file itself, so a package that
builds its desktop bundle to a different path still fails.

---

## Applying

All six are independent; none touches files changed by another, and none depends on any other patch in
`core/patches/`. Verified on a scratch clone of current `upstream/main`: each patch passes
`git apply --check -3` on its own, and all six `git am -3` cleanly in sequence. After applying, the three Python
test files pass (22 tests), the two changed vitest files pass (13 tests), and desktop `tsc -p . --noEmit` is clean.

```sh
cd ~/.hermes/hermes-agent            # or your checkout
git am -3 ~/code/hermes-mods/core/patches/0005-*.patch   # any subset, any order
git am -3 ~/code/hermes-mods/core/patches/{0001,0002,0003,0005,0006,0009}-*.patch
```

After that:

- The Python fixes (0005, 0006, 0009) take effect when the gateway or backend restarts.
- The desktop fixes (0001–0003) need a rebuilt app: `hermes desktop --build-only --force-build`. On macOS, set
  `APPLE_SIGNING_IDENTITY`, otherwise the build is ad-hoc signed.
- None of the six changes the RPC contract, i18n locale files, or the keybind registry, so there's nothing to
  regenerate.
