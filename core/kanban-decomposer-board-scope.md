# Background decomposition keeps board selection local

Patch **1680** (`23a9b9251a2e0c16beb2acd36489d53c96edd9dd`).

## Behavior

A background auto-decompose tick selects its board without changing interactive skill discovery. Concurrent `commands.catalog` and `complete.slash` calls do not suddenly expose `environments: [kanban]` skills merely because decomposition is running.

## How it works

`auto_decompose_tick()` wraps each board's work in `_kb.scoped_current_board(slug)` alongside `_unowned_board_work(slug)`, rather than assigning process-global `HERMES_KANBAN_BOARD` and restoring it afterward. Decomposer helpers that connect without a board argument still see the selected board in their execution context. The context manager restores selection; per-board exception isolation remains.

## Code

`gateway/kanban_watchers_dispatcher.py::_KanbanDispatcher.auto_decompose_tick`.

## Tests

`tests/gateway/test_discovery_during_decomposition.py::test_discovery_stays_equal_while_decomposer_selects_board` runs a dispatcher tick in another thread, pauses it inside `list_triage_ids`, and checks the real registered `commands.catalog` and `complete.slash` methods before, during and after. It verifies that the process environment stays unpinned, the interactive context remains on `default`, decomposition sees the named board, and the worker thread returns to `default` afterward. Actual model decomposition is stubbed.

This source test was not rerun in the mirror. It covers those two discovery RPC entry points, not every discovery consumer or an end-to-end LLM decomposition.

## Applying

Apply 1680 in the [ordered mirror series](README.md), using the existing `scoped_current_board` implementation. No config migration or plugin is added. Patch 1679 adjusts billing-test eligibility separately; see [test stability](test-stability-1659-1681.md).

## Gotchas

Restoring a process-global environment variable in `finally` is insufficient: other threads can observe it while it is set. Keep board selection context-local, including when adding new background helpers. The regression test exercises successful cleanup; exceptional cleanup is not separately tested by this patch.
