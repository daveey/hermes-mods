# Scratch archive shape and custody regression tests

Mirror snapshot 4059; covered ordinals: 3927.

## Behavior

Test-only coverage ensures workspace archival refuses ambiguous Git shapes and changed Git indirection, never modifies the source index, and does not bundle commits from an outside Git directory. Bare repository metadata is archived as payload rather than recursively mistaken for workspaces.

## How it works

`tests/hermes_cli/test_kanban_scratch_archive_shapes.py` uses real local Git repositories/worktrees and injects mutations at scan/archive boundaries. It checks the existing `hermes_cli/kanban_scratch_archive.py` contract, including isolated index use and `.git` preimage checks; the patch changes no production archive implementation.

## Code

These ordinals change only the test files listed below; the existing implementation boundaries are identified under How it works.

### Mirrored commits

- 3927: `af10d6061d949f3c84d0edbfeba38dce8bd7b912` — Pins archive Git-shape, source-index, and external-history boundaries.

## Tests

- `tests/hermes_cli/test_kanban_scratch_archive_shapes.py`: a candidate resolving to another bare Git directory is refused. Forced `update-index` inside the archive path writes only a private index and leaves original bytes/mtime unchanged, with no temporary index leaked into output. A `.git` file changed after judgment refuses before archive publication. External-gitdir unpushed commits are recorded as metadata but not bundled, with the outside Git directory unchanged. A `.git`-named file inside bare hooks remains ordinary tar payload rather than spawning nested repository metadata.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

These contracts are intentionally conservative: an external worktree does not authorize reading and bundling all outside repository history. The fixtures create local-only data; they do not establish archive completeness for arbitrary remote repositories.

## Plugin?

No. Archive classification and payload construction need one core custody boundary.
