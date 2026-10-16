# Session tab underline matches the sidebar status stripe

Patch **0106**.

## Behavior

The active underline on a session's pane tab now uses the same color as that session's sidebar status stripe: green when done, red when stuck, dark grey when parked with ⌘B or waiting on blocked Kanban cards. Draft and running session tabs use a neutral grey. Tabs that aren't sessions keep the theme's primary accent.

## How it works

`lib/session-status-stripe.ts` maps a stripe state to one CSS variable, `--session-status-color`. The sidebar row's stripe (and the periodic-loop glyph) paints from that variable instead of hard-coded Tailwind colors. `SessionTabAccent` wraps a session tile's `PaneTab` and works out the stripe from the same inputs as the sidebar row: dot state, message count, the ⌘B blocked flag, `kanban_blocked` and `cron_active`. It points the tab's `--pane-tab-active-accent` at that variable, so both surfaces share one palette and one predicate (`sessionStatusStripe`).

## Code

Paths relative to `apps/desktop/src/`:

- `lib/session-status-stripe.ts`: stripe → color variable.
- `app/chat/session-tab-accent.tsx`: per-tab accent wrapper.
- `app/chat/session-tile.tsx`: wraps session tabs.
- `app/chat/sidebar/session-row.tsx`: stripe and periodic glyph read the shared variable.

## Tests

```bash
cd apps/desktop
npx vitest run src/app/chat/session-tab-accent.test.tsx
```

Covers the accent on the real workspace and tile tab wrappers, the per-state colors, live updates when a turn starts, ⌘B blocking or row metadata change, and non-session tabs keeping the theme accent.

## Applying

Apply the ordered series with `git am -3` from the merge-base (see [README.md](README.md)); 0106 touches the sidebar row, so it is not an independent apply set.

## Gotchas

- Archived sessions get no stripe, so their tab is neutral.
- Add new stripe states in `sessionStatusStripe` and `sessionStatusStripeColor` together, or the tab and the row disagree.
