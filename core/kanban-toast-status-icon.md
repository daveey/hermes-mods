# Status icon on Kanban task toasts

Patch: `0062`

## Behavior

The popup Hermes shows when a Kanban task reaches a terminal event leads with an icon that says where the task
stands:

| Event | Icon |
|---|---|
| `completed` | green check (done) |
| `blocked`, `timed_out` | yellow `?` (waiting on someone / will retry) |
| `block_loop_detected` (routed to triage), `crashed`, `gave_up` | red X |

The native OS notification (fired while you're away from Hermes) gets the same glyph as a title prefix:
✅ / ❓ / ❌.

## How it works

`completion-notify.ts` already maps each terminal event kind to a toast. Each entry now also carries a codicon name
(`pass-filled`, `question`, `error`) and an `accentColor` (`var(--ui-green)`, `var(--ui-yellow)`, `var(--ui-red)`),
which `host.notify` already supports: the toast renders the codicon instead of the kind's default icon and tints it.
OS notifications can't take a custom icon, so the title gets an emoji instead. No backend change.

## Code

- `apps/desktop/src/plugins/kanban/completion-notify.ts`: `NotifySpec` (`icon`, `accentColor`, `glyph`),
  `DONE` / `WAITING` / `TRIAGE` presets in `TERMINAL_NOTIFY`.

## Tests

`npx vitest run src/plugins/kanban/completion-notify.test.ts` — `status icon` table covers every terminal kind
(toast icon + tint, OS title prefix).

## Applying

`git am -3 0062-*.patch`. Context lines overlap `0058` (toast Open action) in the same file; apply that first.

## Gotchas

- To recolor a kind, change its preset in `TERMINAL_NOTIFY`; `crashed`/`gave_up` share the red X with triage.
