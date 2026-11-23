# Desktop: unsent session drafts above Recents (mirror 5692–5699)

## Behavior
- Client-only draft rows for the main and tab drafts appear below Pinned with project placement, focus, preview titles and optimistic first-send replacement. `listed:false` drafts stay out of backend lists and project snapshots.
- Draft placement uses the same owner/project inputs as real rows (backend project owners and known repo/lane roots), resolved at render time and reactive to tree, profile, connection and configured default.
- The final follow-up removes the earlier borrowed-tile display snapshot: main draft placement mirrors the live first-send cwd expression. A tile publishing cwd can therefore move the main draft too; display placement does not override the send workspace.

## How it works
`sidebar/draft-rows.tsx` and `draft-sections.tsx` render rows. `session/new-session-cwd.ts` reads current workspace intent afresh and subscribes to its fallback inputs. Earlier `store/sidebar-draft.ts` snapshot wiring is removed by the later patches in this group.

## Code
- `apps/desktop/src/app/chat/sidebar/draft-rows.tsx`
- `apps/desktop/src/app/chat/sidebar/draft-sections.tsx`
- `apps/desktop/src/app/session/new-session-cwd.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/index.ts`

## Tests
Static source evidence (not executed for this documentation audit):

`apps/desktop/src/app/chat/sidebar/draft-rows.test.tsx` checks client-only visibility, preview/focus, replacement, and project bucketing. `apps/desktop/src/app/session/hooks/new-session-target.test.tsx` compares the session.create cwd against `prePrSendCwd`, including tile/project changes, and checks tree/default/profile/connection reactivity. Backend calls are mocked in these React tests; a real first-send/native Desktop path is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Several fixes narrowed an earlier design; apply all eight in order.
- Display placement must never alter the send workspace.

## plugin?
No. Desktop UI in the core app.
