# Hub admission: capacity class metadata on cards

Patches: **2951, 2952, 2953, 2958, 2970, 2971, 2976, 2977**.

## Behavior
- Cards persist an admission class and a `frees` claim; they survive readback, and invalid metadata is refused before insertion.
- Edits validate the resulting class/frees pair atomically; a failed edit leaves the task and events untouched.
- CLI create/edit accept the class and frees options; capacity without frees refuses cleanly. Combining them with `--requires` (a separate edit transaction) is refused.
- A legacy courier cannot silently drop admission metadata; a courier create envelope keeps the capacity class and retries match.
- Foreign chat links stay out of pickup copies (a local origin column).

## How it works
2951 adds `kanban_admission_class` and DB columns/sync; 2952 adds atomic edit validation; 2953 exposes CLI creation; 2958 refuses lossy courier placement; 2970 adds the local origin column and courier envelope handling; 2971 updates the Board row-field snapshot; 2976/2977 refuse the separate-transaction combination, moved into `edit_preflight`.


## Code
- `docs/kanban-hub-admission-v2.md`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_admission_class.py`
- `hermes_cli/kanban_admission_origin.py`
- `hermes_cli/kanban_board_row_fields.json`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_edit.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_db_values.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_requires_edit.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_admission.py`: `test_admission_edit_validates_resulting_pair_atomically`, `test_capacity_class_and_frees_claim_survive_task_readback`, `test_cli_admission_refuses_separate_transaction_edits`, `test_cli_create_admission_metadata`, `test_cli_create_capacity_without_frees_refuses_cleanly`, `test_cli_edit_admission_metadata`, `test_cli_frees_alone_refuses_requires_edit`, `test_invalid_admission_edit_preserves_task_and_events`, `test_invalid_admission_metadata_refuses_before_insertion`, `test_legacy_courier_cannot_silently_drop_admission_metadata`.
- `tests/hermes_cli/test_kanban_origin_creation.py`: `test_courier_create_keeps_capacity_class_and_retry_matches`, `test_retry_with_capacity_class_after_pre_admission_intent_conflicts`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2971 mirrors a deployed Board's accepted fields; older Boards may not accept them. 2971 and 2976 have no new test names of their own (2977's test covers the refusal).
