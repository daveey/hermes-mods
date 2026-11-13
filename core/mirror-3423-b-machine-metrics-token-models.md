# Machine metrics: bounded token totals by model

Patches: **2993, 3011, 3037**.

## Behavior
- Machine throughput metrics report token totals grouped by model across all profiles, with unknown models under a literal unknown bucket.
- Model names are truncated (collisions merge); beyond 32 models the smallest are folded into a literal `other` bucket, and exactly 32 are not folded.
- Values are clamped integers; legacy tokens count as unknown model; duplicate inodes are not double counted.
- On a 400 the sender retries without the model map, preserving scalars. The compatibility retry budget (model-map, counter and drain/admission downgrades, CPU fields dropped on the first retry) is documented as four bounded sends permitting 8 s of send time.
- A timed-out bounded sync call no longer overwrites its worker's already-stored schedstat with None.

## How it works
- 2993 extends the throughput, drain and base metrics modules with a bounded by-model aggregation.
- 3011 corrects the sender's documentation of the retry budget; runtime behavior is unchanged.
- 3037 keeps the worker's own schedstat value in `_bounded_call` when the caller's read finds the task entry gone (the worker already returned).

## Code
- `hermes_cli/machine_throughput_metrics.py`
- `hermes_cli/machine_drain_metrics.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/machine_metrics_sender.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_machine_tokens_by_model.py`: `test_groups_all_profiles_and_unknowns`, `test_truncation_merges_collisions_and_unknown_literal`, `test_top_models_fold_without_losing_literal_other`, `test_exactly_32_models_are_not_folded`, `test_clamped_integer_values_and_scalar_equality`, `test_legacy_tokens_have_unknown_model_and_duplicate_inode_not_counted`, `test_cache_nested_values_are_not_shared`, `test_400_retries_without_models_preserving_scalars`, `test_read_contract_and_empty_ledger`, `test_ranking_uses_both_axes_and_tail_preserves_totals`, `test_pre_counter_board_keeps_legacy_fallback_and_backoff`, `test_retry_bounded_and_other_errors_not_retried`.
- `tests/hermes_cli/test_machine_metrics_sender.py`: `test_a_worker_that_exits_at_the_deadline_keeps_its_own_schedstat`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
3011 is a documentation-only change and has no tests.
