# Kanban placement: origin forwarding leases and foreign deferral refusal

Patches: **4653, 4654, 4655, 4656, 4672**

- 4653 (`c479ff8bd6`): [daveey] Move placed-rejection rendering beside placement helpers to offset facade growth
- 4654 (`11d489ff13`): [daveey] register origin forwarding in integration guard inventories
- 4655 (`eafd008e45`): [daveey] Recheck origin forwarding lease expiry at durable admission
- 4656 (`fc1fdc1b17`): [daveey] Refuse foreign-origin deferred placement before card creation
- 4672 (`99c4288981`): [daveey] Preserve explicit placement in frozen self-work producer requests

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- A deferred local placeholder whose origin is a different gateway is refused before the task row is inserted, instead of parking forever after its parents finish. Same-host / no-origin deferrals and immediate qualified forwarding are unchanged.
- An origin-forwarding decision never freezes if the forwarding lease expired while evidence was being read or while waiting for the write lock; expiry is rechecked at durable admission.
- Frozen self-work producer requests keep explicit worker placement and terminal/file capability lists (optional string lists, validated at prepare and advance) instead of being rejected by the v1 allowlist or silently dropped; the capability advertisement gains the additive placement fields.
- The new forwarding module is registered in the lock-guard, handler-ledger and tests-tree-layout inventories.

## How it works

- `_placed_rejection` (the tool-error text for a placed create the target refused, including the idempotency-conflict case that names the existing card) moves from `tools/kanban_tools.py` into `tools/kanban_tools_placement.py` to offset facade growth; behavior is identical.
- `kanban_create` consults a guard in the placement helpers before the INSERT: foreign origin plus requested deferral raises a placement refusal.
- `kanban_origin_forward_decision` re-reads the lease expiry after the evidence read and after acquiring the write lock, and declines to freeze a decision if either shows expiry.
- `kanban_selfwork_producer` adds the optional placement fields to the frozen-request schema, validates them at both boundaries with the existing create validators, and the CI flaky-intake doc describes them.

## Code

- `tools/kanban_tools.py`
- `tools/kanban_tools_placement.py`
- `hermes_cli/kanban_origin_forward_decision.py`
- `docs/ci-flaky-intake.md`
- `hermes_cli/kanban_selfwork_producer.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/hermes_cli/serve_handler_ledger.json`: modified only
- `tests/hermes_cli/test_kanban_lockguard.py`: modified only
- `tests/hermes_cli/test_kanban_selfwork_producer.py`: `test_composed_cli_owner_create_append_and_replay`, `test_unsynced_intake_stays_pending_without_local_fallback`, `test_optional_placement_frozen_unchanged`, `test_placement_validation_at_both_boundaries`
- `tests/hermes_cli/test_origin_forward_decision.py`: `test_expiry_during_evidence_read_never_freezes_decision`, `test_expiry_while_waiting_for_write_lock_never_freezes_decision`
- `tests/test_tests_tree_layout.py`: modified only
- `tests/tools/test_placement_qualified_callers.py`: `test_foreign_deferred_create_refuses_before_creating_placeholder`, `test_foreign_origin_without_deferral_still_creates_local_card`, `test_supported_deferred_create_still_records_placeholder`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Pure inventory/registry edits (4654) must stay in step with the new module names or the guard tests fail.
- The foreign-origin refusal is a hard refusal, not a fallback to a local card.
- Placement fields in frozen requests are additive; consumers that do not know them must ignore, not reject.
