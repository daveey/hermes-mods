# Bounded published-worktree placement observations

Patches: **2226–2229**.

## Behavior

Publication checks used to exempt verified published worktree roots from host-local placement share one three-second observation budget per synchronous creation. Nested decision/audit calls reuse the same observation; a later creation starts fresh. Unverified roots stay host-local.

## How it works

A `ContextVar` scope tracks the shared deadline and results by input text. The CLI create handler, database creation function and tool create handler enter the same nestable scope. Git probes disable filesystem-monitor hooks and use the remaining deadline. Subpaths, submodules, publication races and expired budgets retain the local-placement fence.

Environment setup is imported lazily inside the probe. Small creation helpers move into `kanban_db_creation.py` to avoid cold-import cycles; CLI import formatting preserves its line budget.

## Code

`hermes_cli/kanban_published_worktree.py`, `kanban_placement.py`, `kanban_db.py`, `kanban_db_creation.py`, `kanban.py` and `tools/kanban_tools.py`.

## Tests

Source coverage, **not run in this mirror**: additions to `tests/hermes_cli/test_kanban_published_worktree.py` cover defensive publication fences, HEAD/branch/dirty races, scope reuse and cleanup, shared deadlines across texts and expired budgets. The added scope tests wrap helper calls; they are not separate end-to-end tests of all three creation entry points. Patches 2228–2229 add no dedicated cold-import or CLI entry-point tests.

## Applying

Use the [README full replay procedure](README.md#applying), including its integration bridges. Patch 2226 introduces the publication helper and clean, upstream-reachable worktree tests. Patches 2227–2229 bound its observation budget and adjust its creation seams; do not apply them independently.

## Gotchas

Publication is an observation, not a fetch, checkout or push. Results are not cached across creations. Structural host requirements and other host markers remain authoritative; a published root is not a blanket portability guarantee.

## Plugin

Core placement and creation wiring; no plugin required.
