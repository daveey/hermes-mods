# Persistent Desktop workspace-top contribution area

Mirror entries: 5297. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Desktop plugins can contribute to `workspace.mainTop`, a persistent strip inside the workspace zone below its titlebar/header and above the active chat/page. It remains mounted across session/page switches. Empty contributions add no box; null-rendering guests reserve no padding, border or minimum height.

## How it works

The SDK exports `MAIN_PANE_TOP_AREA`. The workspace group renders `MainPaneTop` outside the active pane lifecycle, filters renderable contributions, preserves ordered registry output and keys each boundary by source plus contribution ID. Existing contribution boundaries isolate guests. A separate window-controls drag band leaves contributor content in a no-drag region.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `apps/desktop/src/components/pane-shell/tree/renderer/main-pane-top.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/tree-group.tsx`
- `apps/desktop/src/sdk/areas.ts`
- `website/docs/developer-guide/desktop-plugin-sdk.md`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

The renderer source test checks area export, ordered placement only in the workspace zone below the titlebar, state retention across session/page switches and empty/null layout behavior. Real packaged Desktop/plugin installation and native window dragging are untested.

Relevant source suites/fixtures:

- `apps/desktop/src/components/pane-shell/tree/renderer/main-pane-top.test.tsx`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

This is zone-owned chrome, not a per-chat remount area or a global strip in every pane zone. Guests should return null when hidden and provide their own desired visible sizing.

## Why core instead of a plugin

The plugin provides content, but the core pane-tree renderer must expose the stable lifecycle/mount slot and SDK area identifier. A plugin cannot create that host boundary on its own.
