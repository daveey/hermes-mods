# Kanban admission and display read-cost reductions

Patches: 5979, 5994, 5995, 5996, 6010, 6011, 6164.

## Behavior

- The admission score snapshot reads open rows, rows named by the caller and rows needed for local custody, plus edges whose child is open or absent; done/archived rows that cannot change a score are no longer fetched. A residual schema probe uses the one-row column reader.
- The annotation holder forces the links projection only when the links cache's own inputs (board revisions, origin ledger) move; plain-input writes leave it to the cache TTL.
- Links receipt de-duplication reads each board once for all aliases with a bounded last-good memo for busy boards.
- Session wait display captures containment histories and receipts once per batch, and request-wait history and watch inputs are read once per kind within an invocation-owned snapshot.
- Board history materialization groups indexed rows before ordering by arrival id per task instead of one global ORDER BY.

## How it works

kanban_admission_score, kanban_session_activity, kanban_links_associations, kanban_contained_display, kanban_wait_history_batch and board_read implement the batching; literal PRAGMA reads remain for minimal facades.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_annotation_parts.py`
- `hermes_cli/kanban_contained_display.py`
- `hermes_cli/kanban_contained_wait.py`
- `hermes_cli/kanban_containment_replica.py`
- `hermes_cli/kanban_external_display.py`
- `hermes_cli/kanban_external_watch.py`
- `hermes_cli/kanban_links_associations.py`
- `hermes_cli/kanban_queue_read_rows.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_wait_rows.py`
- `hermes_cli/kanban_wait_history.py`
- `hermes_cli/kanban_wait_history_batch.py`
- `plugins/kanban/dashboard/board_read.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_kanban_annotation_parts.py`
- `tests/hermes_cli/test_kanban_contained_display.py`
- `tests/hermes_cli/test_kanban_links_association_reader.py`
- `tests/hermes_cli/test_kanban_local_dedup_cost.py`
- `tests/hermes_cli/test_kanban_queue_graph_detail.py`
- `tests/hermes_cli/test_kanban_queue_schema_reads.py`
- `tests/hermes_cli/test_kanban_score_open_snapshot.py`
- `tests/hermes_cli/test_kanban_score_sources.py`
- `tests/hermes_cli/test_kanban_wait_history_batch.py`
- `tests/plugins/test_board_history_payload_sort.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Results are intended to be unchanged; the changes remove rows and statements, not semantics.
- Strict card_state and sample reads stay literal.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban read paths shared by dispatch and the dashboard inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
