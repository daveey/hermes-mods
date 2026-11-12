# Kanban transitive, bounded and capacity-aware priority

Mirror entries: [2269](patches/2269-fix-kanban-inherit-dispatch-priority-across-dependency-chains-daveey.patch), [2286](patches/2286-fix-kanban-preserve-lane-priority-after-Board-capacity-refusal-daveey.patch), [2329](patches/2329-fix-kanban-bound-priority-escalation-and-gate-wait-advice-on-lane-rank-daveey.patch), [2330](patches/2330-fix-kanban-accept-BOM-prefixed-normalization-manifests-daveey.patch).

## Behavior

- Compute effective priority from the maximum active downstream waiter across the dependency chain without rewriting stored parent priorities; terminal intermediates stop inheritance.
- A Board capacity refusal fences lower-priority candidates in that lane for the current pass. Card-specific refusals still allow other candidates; review and ready fences are lane-local.
- Cap new, edited, imported and inherited priorities at 1000 while preserving negative deprioritization. --priority-bump is a positive relative raise, atomically computed and audited.
- Ready-wait advice favors placement and capacity; only captured below-median evidence from the same ready/assignee/placement lane recommends a bump.
- Offer explicit reversible normalization of open positive ranks into 1..900, reserving headroom; accept UTF-8 BOM-prefixed manifests.

## How it works

A recursive UNION query traverses open waiting descendants and terminates on imported cycles. Dispatch stops a refused lane rather than giving a newly freed slot to the next lower row, and preserves that fence across review resume. kanban_priority centralizes the upper bound and same-lane median. Creation, editing, import and effective-priority queries use the cap; relative edits record previous priority, requested relative bump and applied delta. kanban_priority_migration.plan/save_manifest/apply separate snapshot, exclusive durable backup and fenced apply/undo; 2330 changes only the manifest reader to utf-8-sig.

## Code

- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_limits.py`
- `docs/kanban-priority-rollout.md`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_chat_wait.py`
- `hermes_cli/kanban_contract_cli.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_edit.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_priority.py`
- `hermes_cli/kanban_priority_migration.py`
- `hermes_cli/kanban_queue_position.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_transitive_priority.py` — `test_two_level_priority_and_raise`, `test_link_inherits_before_and_after_park`, `test_terminal_intermediate_stops_inheritance`, `test_higher_parent_and_unlink` (additional cases in this file).
- `tests/hermes_cli/test_kanban_capacity_order.py` — `test_capacity_release_restarts_in_priority_order`, `test_card_specific_refusals_do_not_fence_other_candidates`, `test_capacity_fence_is_lane_local_and_survives_review_resume`.
- `tests/hermes_cli/test_kanban_parent_first_dispatch.py`
- `tests/hermes_cli/test_kanban_priority_boundaries.py` — `test_cli_relative_bump_and_audit`, `test_sync_import_clamps_without_echo_loop`, `test_migration_refuses_other_writer_shadow_and_foreign`.
- `tests/hermes_cli/test_kanban_priority_cap.py` — `test_create_edit_and_relative_bump_cap`, `test_invalid_bump_is_atomic`, `test_legacy_transitive_value_cannot_escape_cap`, `test_sync_fields_cap` (additional cases in this file).
- `tests/hermes_cli/test_kanban_priority_migration.py` — `test_roundtrip_ranks_ties_headroom_and_idempotence`, `test_concurrent_priority_edit_refuses_whole_plan`, `test_empty_and_all_nonpositive`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Priority creates no capacity and a capped card can still wait. Normalization is not an implicit connection-time migration: save and attach originals before cap-aware imports can clip them. Apply only on the authorized origin writer; changed priorities, new open cards, foreign-owned rows, wrong identity and shadows refuse. Undo above-cap values requires allow_legacy_rollback=True and coordinated policy rollback. 2330 adds no dedicated new test; the existing migration suite is the relevant regression target, and BOM acceptance was not executed here.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
