# Agent-owned repair for expired missing parents

Patch **2177**. Extends readiness tracking described in [kanban-home-gateway-maintenance.md](kanban-home-gateway-maintenance.md); distinct from the input refusal in [kanban-dependency-block-unresolved-parent.md](kanban-dependency-block-unresolved-parent.md).

## Behavior

When an unresolved parent has exceeded the existing missing-parent grace threshold, blocking the child now also creates agent-owned investigation/recovery work. The blocked event identifies `repair_task_id`; it no longer recommends unlinking the dependency simply to unblock the card.

An existing open repair for that child is reused. A closed repair can be replaced for a later incident. If repair admission fails, the child's block rolls back so readiness can retry. A parent returning before expiry does not trigger repair work.

## How it works

`kanban_db_graph.note_missing_parents` composes `missing_parent_repair` in the readiness transaction after its conditional child update. The helper finds prior `parent_missing` block receipts whose repair is neither done nor archived. Otherwise `create_task` uses its savepoint to admit a scratch repair for the supervisor profile, preserving tenant and creator-card provenance, with terminal/file/kanban requirements. The repair has no dependency on the stuck child and no human request.

Its brief requires checking the owning gateway, full replica, authoritative board evidence, sync freshness and pickup eligibility. Missing local data alone is not treated as proof of permanent board loss. The repair must restore publication/dependency state and verify the child resumes rather than discarding required dependencies.

## Code

`hermes_cli/kanban_db_graph.py` (`note_missing_parents`, `missing_parent_repair`).

## Tests

**Upstream-supplied coverage; not executed in this mirror documentation pass.**

- `tests/hermes_cli/test_kanban_missing_parent_detector.py`: `test_expired_missing_parent_files_one_repair_card`, `test_reopened_child_reuses_open_repair_but_replaces_closed`, `test_failed_repair_admission_rolls_back_block`, `test_returned_parent_does_not_raise_repair`.
- `tests/hermes_cli/test_kanban_restart_before_push.py::test_worker_created_hub_parent_survives_process_exit_before_push` models persisted board publication/pickup and parent resolution after a worker exits before pushing. It adds regression coverage, not new publication runtime code in this patch.

No actual repair agent, cross-host restart or end-to-end operator UI was run here.

## Applying

Use the full ordered replay in [README.md](README.md), including its replay plan and bridges. This change depends on existing graph/readiness machinery and is not an independent application unit.

## Gotchas

The child remains blocked until recovery resolves the problem; creating a repair is not itself recovery. Open repair reuse is per child, not a separate repair for each missing parent. Do not remove a valid dependency merely to make readiness pass.

## Plugin?

No. Blocking and repair admission must share the core readiness transaction so an admission failure cannot strand the child without an owner.
