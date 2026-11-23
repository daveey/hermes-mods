# Creator grouping for blocked cards without visible chats

Source patch ordinals: 5553.

## Behavior

Blocked cards with no resolved visible managing chat group by creating profile and gateway. Selecting that group opens a card on the Kanban surface instead of a nonexistent chat. Real managing sessions keep their session navigation.

## How it works

`fetchBlockedTasks` retains `created_by` as `creator` and distinguishes an answered labels request that omitted an ID from a failed labels request. `groupBlockedTasks` keys chatless cards by gateway plus creator. Headers use creator names, with a gateway suffix where appropriate, while missing creator metadata retains the generic no-session fallback.

## Code

- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/blocked-section.tsx`
- `apps/desktop/src/plugins/kanban/types.ts`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`apps/desktop/src/plugins/kanban/api.blocked.test.ts` covers omitted manager labels retaining creator identity. `blocked-section.test.ts` covers creator/gateway grouping, header/card navigation, missing creator fallback and failed-label or untitled-session navigation.

## Applying

Apply API type, fetch transformation and blocked-section rendering together; rebuild the Desktop Kanban plugin bundle. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

A labels transport failure does not prove a session is absent: preserve the raw managing ID in that case. Creator ownership is presentation metadata, not lifecycle mutation authority.

## plugin?

Yes, the built-in Desktop Kanban plugin is the UI owner; the change relies on its existing host navigation and task-opening interfaces.
