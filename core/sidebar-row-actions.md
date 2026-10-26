# Sidebar section rows: hover actions and strong labels

Patch **0131**.

## Behavior

`SidebarSectionRow` (the row component plugin-owned `sidebar.sections` lists use) takes two new optional props:

- `actions`: trailing controls revealed on row hover or keyboard focus. When the row also has `meta`, the meta text hides while the controls show, so the row width doesn't change. Without `meta`, the controls fade in. Clicks inside the controls should `stopPropagation()`. They render outside the row body, so they never trigger `onSelect`.
- `strong`: semibold, foreground-coloured label for unread items.

Rows that pass neither prop render exactly as before. The hermes-mods `inbox` plugin uses both: a per-row Archive button and bold unread titles.

## How it works

`SidebarSectionRow` builds `metaNode` (adds `group-hover/contrib-row:hidden` + `group-focus-within/contrib-row:hidden` when actions exist) and `actionsNode` (`data-slot="sidebar-section-row-actions"`, shown on the same group states). Both go into `SidebarRowShell`'s existing `actions` slot. `strong` adds `font-semibold text-foreground` to the label.

## Code

- `apps/desktop/src/app/chat/sidebar/contributed-sections.tsx`: `SidebarSectionRowProps.actions` / `.strong`, rendering.

## Tests

```bash
cd apps/desktop && npx vitest run src/app/chat/sidebar/contributed-sections.test.tsx
```

`renders hover actions beside meta without selecting the row, and bolds strong labels` checks four things:
- the actions slot exists and carries the hover-reveal classes;
- the meta hides on hover only when actions exist;
- clicking the action doesn't call `onSelect`;
- only the `strong` row gets `font-semibold`.

This is a class/DOM unit test; jsdom does not evaluate the hover CSS.

## Applying

Apply the ordered series with `git am -3 --empty=keep` from the merge-base (see [README.md](README.md)). It builds on 0105 (`sidebar.sections`). Rebuild Desktop.

## Gotchas

- An older core ignores both props: a plugin passing them still renders rows, just with no hover controls or bold label.

## Could this be a plugin?

No. The row component is core SDK chrome. Rebuilding the row inside a plugin would drift from the session-row grid.
