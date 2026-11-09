# Delegate subscription reclaim error isolation

Patch **2055**.

## Behavior

A failed attempt to reclaim a delegated child's Kanban subscriptions does not discard durable notifications from healthy boards or remote sources. Failed reclamation remains eligible for a later poll.

## How it works

`_kb_reclaim_stray_subs` catches `Exception`, not just SQLite errors, around count/connect/retarget work. Corruption wrappers, permission failures and board-archival races therefore return `False` after diagnostic logging. The caller retains the stray child keys for retry instead of treating the board as clean.

## Code

`tui_gateway/session_notifications.py`.

## Tests

**Not rerun this pass.** `tests/tui_gateway/test_kanban_reclaim_error_isolation.py::test_reclaim_error_keeps_other_board_and_remote_notices` parameterizes corrupt, archived and permission failures. It calls `server._notif_collect_kanban`, verifies healthy-board texts/claims plus an injected remote notice, retains the child's stray key, then retries without the injected connect failure and verifies reclamation. Remote transport itself is mocked, not integration-tested here.

## Applying

Apply 2055 within [the core replay](README.md), after the existing delegate stray-subscription reclaim machinery.

## Gotchas

Failure isolation does not repair a corrupt board or change its permissions. The failed board is retried; healthy notifications are not proof that all boards were read successfully. Exceptions are logged, not silently treated as successful reclamation.

## Plugin eligibility

Not plugin-only: durable notification collection and retry-key ownership are core gateway behavior.
