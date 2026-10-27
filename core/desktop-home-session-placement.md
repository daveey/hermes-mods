# Desktop: new Home sessions stay visible across sidebar views

Patch **0403** (`2f45b7b686`).

## Behavior

A cwd-only chat whose project is not yet resolved gets one consistent provisional placement in every sidebar view
(previews, lanes, filtering, labels). Authoritative Home ownership is honored, and a Home group is synthesized when
absent. Before, the cwd fallback selected a nonexistent project and the chat disappeared.

## How it works

Placement is computed once in `workspace-groups.ts` and reused by `sidebar/index.tsx`, `projects/overview-row.tsx`,
`session-row.tsx` and `lib/session-project-label.ts`.

## Code

`apps/desktop/src/app/chat/sidebar/` (`index.tsx`, `projects/workspace-groups.ts`, `projects/overview-row.tsx`,
`session-row.tsx`), `apps/desktop/src/lib/session-project-label.ts`.

## Tests

`chat-sidebar.integration.test.tsx`, `workspace-groups.test.ts`, `overview-row.test.tsx`, `session-row.test.tsx`
(vitest; formerly asserted omission, now require a visible destination). Not re-run for this mirror.

## Applying

`git am -3` after 0402; no config.

## Gotchas

Ownership transitions (provisional to authoritative) must keep the row in one place; the tests cover them.

## Plugin?

No.
