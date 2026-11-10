# Desktop: optimistic move to project

Patch **2100**.

## Behavior

Move to project (row menu, drag, shortcut) reassigns the session in the cached project tree (ids, previews, lanes, counts) and patches its cwd before the move RPC returns. Each tree payload that arrives while the move is pending re-applies it until the backend agrees or 30 s pass, so an older in-flight refresh cannot snap the row back. A failed move undoes both and refetches.

## How it works

New `projects-move-optimistic.ts` holds pending moves (`PENDING_MOVE_TTL_MS`) and the pure `reassignTreeSession` (returns the same tree object when it already agrees or the target is unknown). `projects.ts` calls it before the RPC and delegates the follow-suppression helpers there to stay under a line-count ratchet. The slow part was the tree refetch endpoint, which can take seconds on large all-profile databases.

## Code

- `apps/desktop/src/store/projects-move-optimistic.ts` (new), `apps/desktop/src/store/projects.ts`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `projects-move-optimistic.test.ts`: row filed under the destination while the RPC is pending and survives a stale refresh; rollback on failure; `reassignTreeSession` control, id/preview/lane/count adjustment, unknown destination. `projects-cross-gateway-move.test.ts` sequence assertions now ignore the rollback's tree read. The tested entry is the store function; the drag, row-menu and keyboard-shortcut triggers are untested.

## Applying

Apply ordinal(s) 2100 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related: [desktop-project-move-by-folder.md](desktop-project-move-by-folder.md). Desktop app builds are required for any effect. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

Within the 30 s window a server-side rejection that does not raise leaves the optimistic state until TTL. Cross-gateway moves add extra RPCs; the rollback refetch is expected.

## Could this be a plugin?

No. It edits core Desktop stores; the Desktop plugin SDK does not expose the project tree.
