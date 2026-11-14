# Source-workspace launch cleanup and clone-root diagnostics

Mirror ordinals: 3770, 3774.

## Behavior

- A source-backed card uses its manifest/custody branch in memory without writing the fenced `branch_name` field, even when that field is NULL.
- Failure to record workspace preparation after a claim releases the unused grant and ends/counts the run instead of leaking the claim out of the dispatch tick.
- Publish refusal tests require the exact unregistered clone root, including a bare repository itself.

## How it works

`kanban_source_launch._prepare` no longer fills branch_name for source-backed cards. `launch` catches preparation failures before attachment/spawn and `_release_unprepared` queues a released unused grant, records infrastructure failure with claim release/run end, and adds a respawn-guarded preparation code. No source attach receipt is fabricated for work that never began. The later patch adds diagnostic assertions to the existing source publish guard.

## Code

- `hermes_cli/kanban_source_launch.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_source_launch.py` — Replica custody with NULL branch_name still spawns with the exact manifest branch while the field/outbox remains untouched. A preparation exception causes no spawn or attach, one unused release with no source receipt, and a guarded result.
- `tests/hermes_cli/test_kanban_source_publish.py` — Unregistered-clone refusal names the clone root for ordinary worktrees and a bare repository, retaining source_clone_unregistered.
- `tests/hermes_cli/test_kanban_workspace_source.py` — The source path/branch passed to spawn are retained while the branch writer receives no calls.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

This does not weaken source custody fences or authorize publication from an unregistered clone. The cleanup applies before source attachment; later custody failure paths have different responsibilities.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
