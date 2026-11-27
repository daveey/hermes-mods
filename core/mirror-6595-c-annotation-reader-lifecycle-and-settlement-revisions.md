# Annotation readers, batched revisions and settlement generations

**Scope:** Board annotation read path: SQLite UDF registration, warm/waiting reader admission, batched revision probes, board reuse on wakes, parked-edge reads, column probe, monotonic settlement marks.

**Source ordinals:** 6382, 6400, 6401, 6471, 6472, 6473, 6480, 6481, 6482, 6493, 6494, 6549, 6550, 6566, 6582

## Behavior

- The managed-count predicate is registered on a managed SQLite connection once (or when the callback changes) instead of re-creating the function while a statement is active; plain connections keep their old behaviour.
- Zero-budget annotation probes borrow an attested retained reader before lifecycle admission, so an unrelated open/close no longer forces busy fallback; a bounded explicit waiting-reader domain keeps waiting-evidence readers from being evicted by generic readers.
- Revision probes for several supported parts share one sqlite_master read and cached per-table table_xinfo results within a probe snapshot; uncertified coverage returns fresh non-equal unknown keys, trigger damage is detected, and file replacement is fenced. Unsupported part names are not accepted by the part-to-kind lookup.
- Content wakes reuse independently unchanged boards; ordinary legacy memo and busy-budget contracts are retained.
- The parked/held walker reads only unfinished parent edges instead of the full historical graph; scope-renewal column discovery reads one row and refuses non-bare table names with ValueError.
- A task-change epoch (EPOCH 4) adds monotonic per-task settlement generations over id/status/block_kind/completed_at with tombstone preservation, so selected settlement values cannot ABA; the plugin API catalog and developer docs describe it.

## How it works

- `kanban_session_activity.py` and `kanban_reader_lifecycle.py` hold callback identity and admission order; `kanban_waiting_reader.py`/`kanban_reader_retention.py` implement the waiting domain.
- `kanban_annotation_parts.py`/`kanban_annotation_revision.py` batch schema reads per snapshot.
- `kanban_activity_revision.py` and `kanban_task_change.py` emit settlement marks inside the caller's snapshot.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/data/plugin_api.json`
- `hermes_cli/kanban_activity_revision.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_annotation_parts.py`
- `hermes_cli/kanban_annotation_revision.py`
- `hermes_cli/kanban_reader_lifecycle.py`
- `hermes_cli/kanban_reader_retention.py`
- `hermes_cli/kanban_scope_renewal.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_parked.py`
- `hermes_cli/kanban_session_activity_revalidate.py`
- `hermes_cli/kanban_task_change.py`
- `hermes_cli/kanban_waiting_reader.py`
- `website/docs/developer-guide/plugins/public-api.md`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_annotation_board_reuse.py`
- `tests/hermes_cli/test_cached_ro_warm_admission.py`
- `tests/hermes_cli/test_kanban_annotation_revision.py`
- `tests/hermes_cli/test_kanban_annotation_semantic_parts.py`
- `tests/hermes_cli/test_kanban_busy_board_activity.py`
- `tests/hermes_cli/test_kanban_content_key_busy_budget.py`
- `tests/hermes_cli/test_kanban_parked_parent_edges.py`
- `tests/hermes_cli/test_kanban_settlement_revision.py`
- `tests/hermes_cli/test_scope_renewal_columns.py`
- `tests/hermes_cli/test_status_filter_function_registration.py`
- `tests/hermes_cli/test_waiting_reader_domain.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6382-6582, 15 commits: 6382, 6400, 6401, 6471, 6472, 6473, 6480, 6481, 6482, 6493, 6494, 6549, 6550, 6566, 6582) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Warm borrowing must still honour poison/forgotten/tracking states; tests pin these.
- Settlement marks include deleted tasks on purpose; deletion followed by reinsertion must not reset a generation.
- The waiting-reader domain is opt-in and plugin wiring is outside the series; performance figures in source commit text are prior-author measurements, not re-run.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

These are internal read-path invariants of the kernel Kanban store and its public plugin API epoch, not extension points.
