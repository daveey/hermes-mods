# Kanban hub placement, admission and recovery follow-ups

Patches: **3387, 3388, 3409, 3410, 3413**.

## Behavior
- Placement recovery's hub-keep decision uses the re-evaluated board rather than a hardcoded `default`; an unreadable card row (missing column) keeps the card on the hub and does not promote it.
- The hub CPU advisory reports an unreadable priority-pool limit as `hub_cpu_pool_limit_unknown`, so `hub_cpu_unknown` means the CPU sample only (3410 narrows the wording: only the priority-pool settings read is covered, not the card's admission class).
- Hub admission deny routing queues a card on the worker queue only when one fresh machine holds the whole label set (queue label plus coordination labels like the hub-write capability); with no joint holder the previous fallback (worker-first, or mandatory-pin refusal) stands.
- Pickup's re-place target follows the same whole-label rule.

## How it works
- 3387 threads a `board` argument through `available_mode` → `recovery_mode` → `promotion_mode` → `hub_keep_reason`, and renames pickup `_fields` to public `card_fields`.
- 3388 adds `HUB_CPU_POOL_LIMIT_UNKNOWN` to the queue reason codes and splits the advisory failure path; also documents it in `docs/worker-first-v1.md`.
- 3409 documents that `card_fields` raises `LookupError` (`KeyError` for dict rows, `IndexError` for sqlite rows), makes the fail-closed `except` catch it, and extends the board-hop test.
- 3410 is wording-only on the advisory docstring and comments.
- 3413 makes `pool_route` build `runs_on` once and judge the whole label tuple with `queue_decision`'s source; `machine_ok` accepts a string or tuple of labels.

## Code
- `hermes_cli/kanban_placement_recovery.py`
- `hermes_cli/kanban_hub_admission_pickup.py`
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_worker_first_selfcheck.py`
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_queue_reason.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_placement.py`
- `docs/worker-first-v1.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Across `tests/hermes_cli/test_kanban_placement_recovery.py` and `tests/hermes_cli/test_kanban_mandatory_pin.py`: `test_unreadable_card_fields_keep_the_card_on_the_hub`, `test_promotion_reads_the_keep_reasons_on_the_reevaluated_board`.
- Across the hub CPU gate tests (`tests/hermes_cli/test_hub_cpu_gate_contention.py`, `test_kanban_hub_admission.py`): `test_advisory_pool_limit_failure_is_not_reported_as_a_sample_problem`.
- Across `tests/hermes_cli/test_kanban_placement.py`, `test_kanban_hub_admission_pickup.py`, `test_kanban_pool_deny_runs_on.py`: `test_label_machines_counts_only_fresh_members`, `test_machine_picks_up_takes_any_label_collection`, `test_denied_queues_only_when_one_live_machine_holds_every_label`, `test_denied_machine_ok_seam_sees_the_whole_label_tuple`.
- 3410 is wording-only (no tests).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The CPU limit, cap and claim hold limit are unchanged.
- `reevaluate` still only runs on the default board.
