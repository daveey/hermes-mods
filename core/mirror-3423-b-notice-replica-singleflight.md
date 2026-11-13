# Remote notices: single-flight replica snapshots

Patches: **3016–3019, 3034**.

## Behavior
- Concurrent notice pollers share one replica snapshot per lane outside the publication lock; a slow replica holds neither the publication lock nor other replicas.
- A real newer snapshot wins over a delayed ledger write; a failed snapshot releases its lane and retries; idle lanes are bounded.
- Shared reuse is rejected when fingerprints are unknown or a sequence signature is missing, and a replacement during snapshot forgets decode identity.
- The lane is held through generation assignment, so generations follow snapshot order.

## How it works
- 3016 restructures `kanban_remote_observations.py` into per-lane single-flight with a shared memo; wiring in `kanban_remote_notices.py`.
- 3017, 3018 and 3019 add boundary tests (shared signature, uncertain reads, legacy replicas, unknown fingerprints).
- 3034 pins the interleaving order with a deterministic test and makes the decode map a required argument, removing the unused module-level fallback.

## Code
- `tui_gateway/kanban_remote_observations.py`
- `tui_gateway/kanban_remote_notices.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_kanban_observation_singleflight.py`: `test_concurrent_snapshot_is_singleflight_outside_publication_lock`, `test_shared_snapshot_matches_uncached_path_across_changes`, `test_slow_replica_does_not_hold_publication_or_other_replica`, `test_real_newer_snapshot_wins_over_delayed_ledger_write`, `test_failed_snapshot_releases_lane_and_retries`, `test_idle_lane_bound_retains_active_lane_identity`, `test_signature_reuse_still_supplies_cards_to_a_new_ledger`, `test_same_signature_refresh_does_not_repeat_persistence`, `test_unknown_after_fingerprint_forces_a_fresh_snapshot`, `test_replacement_during_snapshot_forgets_decode_identity`, `test_missing_sequence_signature_never_reuses_an_old_memo`, `test_unknown_fingerprints_never_reuse_a_shared_snapshot`, `test_lane_is_held_through_generation_so_older_snapshot_never_outranks_newer`.
- `tests/tui_gateway/test_replica_bounded_reads.py`: `test_warm_observation_identity_scan_is_bounded`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
