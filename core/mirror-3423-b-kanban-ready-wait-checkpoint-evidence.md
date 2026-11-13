# Kanban ready-wait checkpoint selection evidence

Patches: **3003, 3004**.

## Behavior
- A ready-wait notice records bounded diagnostic evidence alongside it: the checkpoint lookup reason (`no_run`, `nonpark`, `unfinished`, `empty_summary`, `included`, `lookup_error`), run id, outcome, end time and run uid, plus emitter pid, host, gateway identity and core revision, without a second lookup or an extra event.
- An unknown boot revision or unenrolled emitter still posts the notice.
- The boot fingerprint is resolved through the canonical install root.

## How it works
- 3003 extends `fields()` in `kanban_last_checkpoint.py` with a diagnostic dict and adds `notice_fields`; the parent-unclaimed sweep records it.
- 3004 resolves the fingerprint root via `project_root_str()`.

## Code
- `hermes_cli/kanban_last_checkpoint.py`
- `hermes_cli/kanban_parent_unclaimed.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ready_wait_checkpoint.py`: `test_notice_records_selection_and_emitter_without_summary`, `test_sweep_records_checkpoint_before_or_after_guard_observation`, `test_unknown_boot_and_unenrolled_emitter_still_posts`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
3004 has no tests of its own. The boot revision is intentionally unknown for unbootstrapped callers.
