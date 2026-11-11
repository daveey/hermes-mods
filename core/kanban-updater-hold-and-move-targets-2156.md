# Updater-hold diagnostics and stricter card-move targets

Patches **2153, 2154**.

## Behavior

- **Updater hold is visible.** When the local core-update admission hold defers a claim, the deferral is recorded per card as `update_admission_hold`, and the "dispatcher stuck" warning lists every recorded per-card hold ("Held cards: id=reason; ...") instead of only bucket counts, ending with a pointer to inspect the updater hold when that reason is present.
- **Move targets are validated against what the card needs.** A `kanban move` target must be an active, unheld registry machine carrying every label. A *running* card must leave this machine; a *queued* card may target it (so it can return home or stay). The target must also attest `capability:host_repo` when the card requires `host_repo` and `skill:<pin>` for every pinned skill, checked per machine so two machines cannot jointly satisfy separate halves. The assignee's profile-tool check stays as before.
- **Local inventory failures do not veto remote candidates (2154).** When the local machine is the candidate, its capability and skill inventory is read from the assignee's profile; if that profile is missing or unreadable (`OSError` / `ValueError`) the local machine is treated as lacking the labels instead of raising, so capable remote machines are kept.

## How it works

`kanban_admission.admission_hold_set(task_id)` calls `_defer(task_id, "update_admission_hold")`; `claim_task` and `claim_review_task` pass the id. `kanban_db_dispatch_stuck.held_cards` aggregates `respawn_guarded`, `claim_deferred`, `skipped_*`, `rate_limited`, per-profile caps and `owner_down`. In `kanban_move.py`, `matching_machines(..., exclude_me=)`, `_requirement_matches` and the `check` flow (`exclude_me = status == "running"`) implement the rules, raising `MoveRefused("capability_mismatch", ...)` that names each machine and its missing labels.

## Code

- `hermes_cli/kanban_admission.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_dispatch_stuck.py`, `hermes_cli/kanban_move.py`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

- `tests/hermes_cli/test_kanban_hold_diagnostics.py`: `test_updater_hold_records_each_unclaimed_card_and_recovers`, `test_warning_names_all_recorded_holds`.
- `tests/hermes_cli/test_kanban_move_validation.py`: `test_queued_move_can_target_self_without_pickup`, `test_move_refuses_missing_worker_labels_without_writes`, `test_move_requires_one_target_with_both_labels_and_profile_tools`, `test_queued_self_still_checks_local_profile_tools`, `test_every_required_label_must_be_on_the_same_target`, `test_queued_self_must_still_be_active_and_unheld`, `test_queued_return_uses_local_inventory_not_worker_labels`; 2154 adds `test_missing_local_profile_does_not_veto_capable_remote`.

## Applying

Apply ordinals 2153 and 2154 together, as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as independent patches. 2154 amends the local-inventory read added by 2153. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- `admission_hold_set` is an advisory lock-free read; `claim_transaction` still rechecks under the admission lock.
- Move validation needs worker-registry labels (`capability:`, `skill:`); machines that do not publish them are refused for cards that need them.

## Could this be a plugin?

No. Admission, the dispatcher warning and move validation are core Kanban internals.
