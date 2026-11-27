# Conditional terminal-run reservation and indexed wait history

Patch coverage: 6213, 6214, 6215, 6312, 6349.

## Behavior

Conditional parking can reserve the final authorized run for its real terminal/probe outcome instead of spending it on an implied timer. Ordinary timer admission remains possible with multiple runs left. Dispatcher checkpoints service wakes between slow phases, not only at the next outer tick.

ETA wait-history capture bounds ordered park candidates and finds the first relevant successor through dedicated opt-in indexes.

## How it works

Reservation is tied to the current scheduled park and budget state. An implied timer held by reservation backs off instead of remaining continuously due. Terminal/probe-repair events retain their path; foreign copies, unsupported implicit relayed parks and exhausted non-run caps do not invent a reservation. Ready-clock lookup uses the complete conditional event registry and its ready predicate.

The partial park-history index selects scheduled/continued events in id order with an explicit typeof(kind) predicate. A second task/id index over fixed end kinds permits one successor seek per park, replacing per-kind minima. Legacy schemas keep their original queries. Explicit predicate opt-in avoids changing unrelated readers' unspecified traversal ordering.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_clock.py`
- `hermes_cli/kanban_dispatch_maintenance.py`
- `hermes_cli/kanban_dispatch_paths.py`
- `hermes_cli/kanban_dispatch_wake_protection.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_terminal_run.py`
- `hermes_cli/kanban_wait_history_batch.py`
- `hermes_cli/kanban_wake_check.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_terminal_reservation_controls.py`
- `tests/hermes_cli/test_kanban_dispatch_wake_protection.py`
- `tests/hermes_cli/test_kanban_parent_unclaimed_ready_since_bounded.py`
- `tests/hermes_cli/test_kanban_wait_history_index.py`
- `tests/hermes_cli/test_kanban_wait_end_index.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

A reservation creates no new budget or external subscription. Earliest successor is event-id order, not timestamp order. Index installation must preserve malformed-payload failures and exact legacy results, including changes visible on the next read.

## Why core, not a plugin?

Dispatch budgets, continuation state, wake checks and schema/query planning are core execution machinery.
