# Desktop "+ Create project" files the row at once

Patches:

- `patches/2179-fix-desktop-Create-project-moves-the-row-into-the-ne.patch`

Follow-up to the instant move-to-project work: "+ Create project" still waited for list, mkdir, create, move and a 2-5 s tree refetch before the row changed project, because the destination was not yet in the project tree.

## Behavior

- `createProjectAndMoveSession` (the Cmd-Shift-P "+ Create project" path in the Desktop store) moves the session row at once, before any RPC. It reuses a same-named existing project node, otherwise it inserts a provisional node (`provisional:<name>`) and files the row there.
- Tree payloads that arrive before the backend lists the new project keep the provisional node and the row in it; once a project of that name is listed, the pending move retargets to the real node and the provisional node disappears.
- A failed create or move removes the provisional node and restores the previous owner (the caller then refetches the tree).

## How it works

`projects-move-optimistic.ts` keeps a `provisionalProjects` map (TTL shared with pending moves). `applyPendingMoves` first merges provisional nodes into every freshly built tree (`withProvisionalProjects`), retargets pending moves when `sameNamed` finds a real node, and `showCreatedAtOnce` returns the undo closure. `renameProjectSessions` was only relocated to `projects-rename-sessions.ts` to stay under a per-file line ratchet; behavior unchanged.

## Code

`apps/desktop/src/store/projects-move-optimistic.ts`, `projects.ts`, `projects-rename-sessions.ts` (new).

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`apps/desktop/src/store/projects-move-optimistic.test.ts`: the provisional node owns the row while `projects.create` is pending, survives a stale refresh and ends on the real node; a failed create removes the node and restores the old owner. Desktop store path only; no other entry path is claimed. The reuse-of-an-existing-same-named-project branch has no dedicated test (untested).

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

Name matching is by normalized label (`labelKey`), so an archived or no-project node is not reused. A provisional node older than the pending-move TTL is dropped even if the backend never listed it.

## Plugin

None.
