# Desktop: one-click Archive beside the session row kebab

Patch **2104**.

## Behavior

Hovering a sidebar session row shows an Archive icon button immediately before the "Session actions" kebab, in both list and card layouts. One click archives the session without resuming it. Already-archived rows show no Archive button. The button is a pointer shortcut only (not a tab stop); keyboard users keep the kebab menu and the existing archive hotkey. The hover cluster lifts over the row's tail (which reserves width), and a PR chip that ends a slot stays visible beside the cluster instead of under it.

## How it works

New `session-row-hover-actions.tsx` exports `RowHoverActions` (Archive button with haptic feedback and `onMouseDown` preventDefault so focus is not parked on a vanishing row, plus the kebab wrapped by `SessionActionsMenu`). `session-row.tsx` is simplified to use it and sizes the hiding tail (`min-w-11`).

## Code

- `apps/desktop/src/app/chat/sidebar/session-row-hover-actions.tsx` (new), `session-row.tsx`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `session-row-actions.test.tsx`: archive in one click for both layouts without resuming; no button on an archived row. `session-row.test.tsx`: PR chip stays visible beside the cluster; the cluster lifts over a hiding tail sized to its buttons. Touch and keyboard entry are not covered.

## Applying

Apply ordinal(s) 2104 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related: [archive-linked-confirm.md](archive-linked-confirm.md), [plugin-archive-hotkey.md](plugin-archive-hotkey.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

One click archives without confirmation; linked-card confirmation behaviour is whatever `onArchive` already does.

## Could this be a plugin?

No. It is core sidebar row markup.
