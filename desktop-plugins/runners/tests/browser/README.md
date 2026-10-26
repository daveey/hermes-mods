# Chromium regression harness

Run `npm ci` in the parent `tests` directory first, then here:

```sh
npm ci --ignore-scripts
npm test
```

Uses `/usr/bin/google-chrome`; override with `CHROME_PATH`. No production requests. Inventory and inspect responses derive from public generated contracts. All names, routes, repositories and IDs are fictional. Enrollment matches the server version-1 contract.

The harness bundles the actual plugin, React, TanStack Query and Radix. SDK theme/chrome wrappers are approximations explicitly labeled on captures: these are **not packaged Desktop/live-data acceptance evidence**. No native command-palette shell is mounted; its registered callback is exercised.

## Fixtures

Default: **57 registrations, 45 explicitly unassigned**, 12 mapped to one physical node plus an empty physical node. Exactly 1 running, 3 idle, 8 held and 45 unknown. Two explicit repository scopes carry 42 and 15 registrations (the unassigned split is 30/15). Every provider registration and host probe has explicit provenance, separate from the collector envelope. Counts are derived from rows, not capacity.

`?cases=1` includes independent failure while held, warning/degradation, expired hold, stale provider evidence with fresh hold, contradictory offline/busy, busy/draining and offline/held. `?long=1` adds empty physical nodes to stress scrolling. `?theme=light` switches palette; `&custom=1` overrides accent and warning tokens.

## Acceptance

- Real Chrome typing, middle caret insertion/deletion, CDP IME composition/commit, composition-key guard, modifier guard, Tab, arrows, Enter/Space and layered Escape/focus restoration.
- Native state and gateway selects, state counts and bar denominators; 45-row unassigned expansion.
- Lazy owner-routed details, disabled unsupported maintenance, Radix context-menu right-click/Shift+F10/Escape, registered palette alias, help.
- Dark/light at 900/640/390 widths and 900/480 heights: bounded panel, no horizontal overflow, stationary header/filter and internal inventory scroll. Expanded evidence checked at all three widths in both themes.
- Custom palette and text/indicator/focus contrast; long inventory and offscreen status anchor.

`report.json` records all passing checks, failures, geometry, contrast and capture paths. Collected independent failures still cause `npm test` to exit nonzero; test recovery never removes a failed gate. `verification.md` records the latest inspected outcome and limitations. Generated HTML, bundle, JSON report, PNG captures and logs remain ignored, available locally in this directory. No screenshots are copied to production design directories by this harness.

OS-native IME candidate windows, screen-reader walkthrough, packaged Desktop shell, native palette shell and live owner integration remain separate acceptance gates.
