# Desktop plugins: re-check plugin.js on power resume and every minute

Patches: 1600

- `patches/1600-daveey-desktop-plugins-re-check-linked-plugin.js-on-.patch`

## Behavior

- **1600 (level-triggered refresh)** fs watch events are treated as a hint, not the truth. On every power resume
  and once a minute (`DISK_REFRESH_MS` = 60 s), Desktop re-reads each known disk plugin's `plugin.js` and reloads
  the ones whose bytes differ from what was last loaded, then re-arms that file's watch.
- Fixes a stale plugin after a `git pull` into a linked checkout right after a wake: macOS FSEvents can drop events
  and libuv discards the "events were dropped" notice, so `fs.watch` never reported the change and the old plugin
  kept running until the file was touched again.
- Unchanged files are not reloaded; each pass costs one read per known plugin.
- A reload from this pass writes a log line:
  `[plugins] <origin>: plugin.js changed with no watch event; reloaded (resume|interval)`.
- A `plugin.js` that is gone or unreadable is not reloaded; the pass hands off to the disk-plugin scan, which
  reconciles or unloads it. An oversize file is skipped silently (already reported on its row at load, so it does
  not toast once a minute).

## How it works

- `DiskPlugin` gains `loadedSource`: `loadDiskPlugin` stores the `plugin.js` text it read (loaded or not).
- New exported `refreshChangedDiskPlugins(reason)`:
  - returns `[]` with no `window.hermesDesktop`, while a scan or another refresh is running, or with no known plugins;
  - if any entry is a linked dev package (`sourceFile`), runs `resyncLinkedDiskPlugins()` first so Electron
    re-copies changed sources and the copy compared is current;
  - for each entry still current in the `disk` map, reads `plugin.js`; on a byte difference from `loadedSource`
    calls `loadDiskPlugin`, then `watchDiskPluginFile` (a rename-over or folder swap orphans the old watch);
  - a read error other than `PluginSourceOversizeError`, or a failed reload, sets `rescan` and triggers
    `scanDiskPlugins()` after the pass;
  - returns the list of reloaded entry files.
- `watchRuntimePlugins()` registers `desktop.onPowerResume?.(() => refreshChangedDiskPlugins('resume'))` and a
  `window.setInterval(..., DISK_REFRESH_MS)` with reason `interval`. Both live as long as the window.

## Code

- `apps/desktop/src/contrib/runtime-loader.ts`
- `apps/desktop/src/contrib/runtime-loader-refresh.test.ts` (new)

## Tests

`apps/desktop/src/contrib/runtime-loader-refresh.test.ts`, describe
`disk plugins: level-triggered refresh (missed fs events)`:

- `entry paths: power resume and the interval each reload a plugin.js rewritten with no watch event`: the power
  resume callback reloads v2, stops the orphaned watch (`w-linked-1`), re-watches the file and logs `(resume)`;
  advancing fake timers by `DISK_REFRESH_MS` reloads v3 and logs `(interval)`; earlier versions register once.
- `unchanged bytes never reload, and a vanished file hands off to the scan instead of reloading`: two refreshes
  with identical bytes return `[]` and do one read each; an ENOENT read returns `[]` and the follow-up scan stops
  the watch.

These were not rerun by the mirror pass.

Untested:

- Linked dev package path (`sourceFile` set, `resyncLinkedDiskPlugins()` before the compare).
- Oversize `plugin.js` skipped without rescan.
- A reload that fails (`loadDiskPlugin` returns false) triggering a rescan.
- The skip guards (scan in progress, refresh already running, no `hermesDesktop`).
- Real macOS FSEvents drops around sleep/wake (the tests simulate a missing event).

## Applying

- Standalone; applies on top of the existing disk-plugin loader in `runtime-loader.ts` (`loadDiskPlugin`,
  `watchDiskPluginFile`, `resyncLinkedDiskPlugins`, `scanDiskPlugins`, `readPluginSourceText`).
- No config. The resume hook needs a shell exposing `hermesDesktop.onPowerResume`; without it only the
  interval runs.

## Gotchas

- `watchRuntimePlugins()` is once per module, so the test lives in its own file; the second test case first
  re-syncs the module's disk map because state carries over from the first case.
- Byte comparison is against the last *read* text, including a text that failed to load, so a broken file is not
  retried every minute unless it changes.
- Worst-case latency for a missed event with no wake is one minute.

## Plugin?

No: it changes the core Desktop disk-plugin loader's watch/reload logic, which plugins cannot hook.
