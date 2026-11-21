# Kanban: assignment repair validates a foreign original from the Board's evidence, typed refusals

Mirror entries 5139-5143.

## Behavior

- When an assignment repair executes on a gateway that does not hold the original task (split custody), completion no longer retries forever. It reads the original and its complete blocked history from the enrolled Board's atomic execution-evidence endpoint before taking the writer lock, binds the short-lived observation to the repair key, and refuses if custody changed.
- Capability/model and execution-authority validation are kept. Stale replica rows are never consulted and the original is never mutated or materialized locally. Scratch wire rows need no host-local path; other workspace kinds refuse absent path evidence.
- 5140: an un-enrolled gateway (`SyncUnavailable`) now yields the typed `AssignmentRoutingError` refusal; sync disabled or board unselected means zero Board reads. The newest matching block is the authority.
- 5141-5143 add only tests: each deleted inventory/evidence validation clause (matching-repair filter, echoed task id, non-dict blocked payload, invalid reason/UID/ordinal/override/list element/runs/kind, malformed provider list/int, scratch path type) fails a dedicated case.

## How it works

- `hermes_cli/kanban_assignment_original.py`: prepare/inventory of the foreign original, deadline- and key-bound observation, typed Board failures.
- `hermes_cli/kanban_assignment_routing.py` and `hermes_cli/kanban_db.py`: consume the observation during completion; no network once a transaction is already held.

## Code

- `hermes_cli/kanban_assignment_original.py`
- `hermes_cli/kanban_assignment_routing.py`
- `hermes_cli/kanban_db.py`

## Tests

Source tests (not executed in this mirror pass), all in `tests/hermes_cli/test_kanban_assignment_custody.py`:

- 5139: `test_foreign_original_completes_normally_without_materializing_it`, `test_foreign_evidence_fails_closed`, `test_foreign_original_authority_is_not_granted_by_profile`, `test_foreign_policy_drift_still_refuses`, `test_foreign_model_validation_is_not_skipped`, `test_board_failure_has_no_replica_fallback`, `test_local_original_never_needs_board`, `test_preflight_cannot_be_reused_for_another_key_or_after_deadline`, `test_no_network_if_caller_already_holds_transaction`, `test_board_http_refusal_is_typed`, `test_scratch_wire_projection_needs_no_host_local_path`, `test_local_materialization_cannot_bypass_canonical_observation`.
- 5140: `test_unenrolled_gateway_is_typed_refusal`, `test_prepare_refuses_with_zero_board_reads_when_sync_not_selected`, `test_newest_matching_block_is_authority`.
- 5141-5143: parametrized rows only.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Live-network behavior against a real Board is untested in the source tests (the Board is faked).
- The refusal is intentionally strict: any ambiguity leaves the repair unfinished rather than guessing.

## Why core instead of a plugin

The completion path runs inside the core kanban database's writer lock and routing policy; the evidence read must happen before that lock, which only core can order.
