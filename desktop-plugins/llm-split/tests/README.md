# Titlebar click/persistence regression

`pointer-browser.mjs` mounts **both actual plugins** in an owned Chromium page. The shell fixture reproduces
Radix Tooltip → outer titlebar button (`onPointerDown: stopPropagation`) → span PopoverTrigger, with the SDK's
Popover portal nesting and React Query. Only REST replies are fixtures. It never drives the user's Desktop.
The shell tooltip has `pointer-events:none`, as in core; a deliberately exposed outer-button strip tests clicks
that land outside the inner span. Browser mouse/keyboard input is trusted, not `dispatchEvent`/`element.click`.

## Run

Install test dependencies into an owned scratch directory (not the live deployment checkout):

```sh
npm install --prefix "$SCRATCH/deps" --no-audit --no-fund \
  react@19.2.0 react-dom@19.2.0 @tanstack/react-query@5.90.5 radix-ui@1.4.3 \
  jsdom@26.1.0 esbuild playwright
HERMES_POINTER_PACKAGE="$SCRATCH/deps/package.json" CHROME_BIN=/usr/bin/google-chrome \
  node desktop-plugins/llm-split/tests/pointer-browser.mjs
HERMES_DESKTOP_PACKAGE="$SCRATCH/deps/package.json" node --test \
  desktop-plugins/llm-split/tests/*.test.mjs desktop-plugins/throughput/tests/*.test.mjs
```

Alternatively install Playwright Chromium and omit `CHROME_BIN`. The browser script is a separate explicit
acceptance check, not silently skipped inside the jsdom suite. Its JSON includes assertion names and raw
trusted pointer/focus/click events. It throws on page errors, failed assertions or synthetic click events.

## Proof and causal boundaries

Executed on Linux Chromium with Node 22: **32 browser state assertions passed**, plus REST-payload assertions
for split Auto/manual drag and throughput form submission; **49 Node tests passed, 0 failed, 0 skipped**.
Running the same browser fixture against pre-fix source `2dba6c7d` fails `split: focus outside stays open`:
expected 1 panel, observed 0. The fixture also exposed first-outside-click failure after panel interaction
before adding the scoped capture listener. Mouse-out by itself did **not** reproduce premature dismissal.

Problem: click-open titlebar chips had incomplete activation/dismissal semantics.

- Why 1 (padding): the shell's outer button owns the click, but plugin `onSelect` ignored all mouse events;
  only the inner span's Radix trigger toggled. Evidence: original `tool` / `titlebarTool` handlers and the
  padded-area browser checks. Handle outer-target mouse clicks while leaving descendants to Radix.
- Why 2 (focus): nonmodal Radix defaults to dismissing on outside focus. Evidence: baseline browser failure
  above. Prevent outside-focus dismissal; movement and focus alone must not change click-open state.
- Why 3 (outside click): React events in portaled content still reach shell ancestors. The shell stops
  pointerdown; stopping it inside the panel does not repair Radix's document pointerdown bookkeeping either.
  Evidence: first outside click after Auto/drag stayed open in the initial real-pointer run. A per-open
  document **click capture** listener checks actual trigger/button and content refs, so the first outside
  click closes, including after panel interaction. It is removed on close/unmount; no layout observer or poller.
- Why 4 (tooltip/focus): portaled panel focus can bubble to the outer Tooltip trigger. Evidence: throughput's
  initial harness run consumed Escape without dismissing the panel; retaining trigger focus on open and
  stopping panel focus propagation makes single-Escape and form-Enter checks pass. The shell fixture's
  pointer-events-none tooltip styling is preserved; tooltip rendering is not substituted with a no-op.

Root (happened): plugin span triggers and portal content participate in a shell-owned button/Tooltip event
tree, but activation/dismissal originally assumed a standalone trigger. The native app-region overlap is a
**separate core defect**, addressed by core PRs #4336 and #4367, not by these plugin changes.

Root (not caught): existing jsdom tests dispatched click alone, largely on standalone plugin components;
they did not exercise trusted pointerdown → browser focus → pointerup → click or the complete titlebar nesting.
Countermeasure proof: the owned-browser matrix covers hover-closed, inner/outer padded clicks, second click,
trigger/panel/outside movement, focus-only changes, outside click (before/after form use), Escape, Enter/Space,
Auto/manual drag, throughput form Enter and range selection for both chips.

Recurrence: follows the initial titlebar placement (`bd38bd73`) and throughput sparkline fix (`1497d031`).
Those changes did not test this event matrix. Prior native geometry work does not establish dismiss behavior.

Production boundary: Chromium proves DOM/React/Radix behavior, **not Electron native window-drag hit testing**.
A core build containing the drag-region reservation fixes must also be installed and observed before claiming
the native Desktop is repaired. No old-core geometry override is included. The reported mouse-out causal root
remains undetermined until production event evidence distinguishes focus dismissal, tooltip behavior and native
drag interception; fixture success alone must not be presented as a live-Mac readback.
