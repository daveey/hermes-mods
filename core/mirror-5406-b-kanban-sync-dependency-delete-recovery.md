# Kanban sync: audit-preserving abandonment of containment-refused dependency deletes

Mirror entries 5204-5208.

## Behavior

- A refused historical unlink left the canonical link live but local deletion intent and its tombstone unresolved, and no audit-preserving abandonment existed. New per-link, token-bound preview and apply: completed diagnostic refresh, authenticated empty-head cuts, endpoint and containment identity proof, local transactional compare-and-set. Apply stores the original operation/event bytes in an immutable hashed receipt and clears only the covered stale tombstone. Normal sync retires only exact receipt-covered operations and keeps the canonical dependency and all history. No task, link or lease writes.
- CLI: an explicit per-link preview/apply parser entry; docs page `kanban-dependency-delete-recovery.md`.
- 5205: settle revalidates sync identity inside its own transaction (a gap found by per-guard mutation testing); 5206-5207 only add tests that distinguish defensive guards and table-drive fixtures.
- 5208: the SQL unlink audit uses a truncated millisecond clock while the event append samples Python seconds, so exact floored-second equality refused genuine output; the producer audit may be in the same or immediately following second, with all identity, count, UID, receipt and transactional fences kept.

## How it works

- `hermes_cli/kanban_sync_dependency_delete.py` (plan/apply/settle), `kanban_sync_rejections.py`, `kanban_sync_cli.py`, `kanban_parser.py`; docs `website/docs/user-guide/features/kanban-dependency-delete-recovery.md`.

## Code

- `hermes_cli/kanban_sync_dependency_delete.py`
- `hermes_cli/kanban_sync_rejections.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_parser.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_sync_dependency_delete.py` (three-delete preview/apply/normal resolver byte preservation, sync under lock, missing unlink audit refusal, corrupt receipt, exact refused empty deletes only, vetoes, revalidation, link-version change after apply, CLI preview/apply, evidence age including lock wait, local CAS, per-guard cases, `test_real_unlink_producer_audit_second_boundary`). The commit text says no live repair was performed; live repair is untested here.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Operator-only; convergence across sync must be verified separately.
- Adjacent-second acceptance means a neighboring audit can cover an operation; tests move all matching audits out of range for the clock veto.

## Why core instead of a plugin

It operates on the core sync tombstone and canonical-link tables with core transaction fences.
