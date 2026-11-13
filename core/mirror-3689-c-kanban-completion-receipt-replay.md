# Kanban replayed completions bind their run's receipt

Patches: **3666, 3683**.

## Behavior
- A completion replayed from a pool worker before `origin_gateway` was recorded on the hub now verifies when the Board's lease of the completing run names exactly one other gateway; before, it stayed unverified and its origin chat listed it as `unknown`.
- A pre-uid replayed completion whose worker-local integer event id happens to name a receipt of another run now falls back to the receipt of its own run, instead of failing closed.
- Completions stay exact (no fallback) when the run was held by this gateway, has no Board lease, the lease names another run or has a non-string holder, two gateways leased the run, or the board cannot name itself.

## How it works
- 3666 adds `kanban_completion_refs.leased_elsewhere` (one SELECT per batch): for each NULL-origin completion it returns the single foreign gateway the Board leased the completing run to (`claimed` event whose run uid equals the run's uid). Such a completion goes to `pick` as `imported_from=<holder>, legacy=True`, so receipts match on NULL origin in the same run. Both `read_completions` and `observe_owner_task` (through `import_kwargs`) use it.
- 3683 changes `pick`: for an imported completion an id hit binds only if its run matches the completion's run; otherwise it falls through to the same-run fallback (latest receipt of that kind by the same gateway in that run before the completion). Local and uid-bound completions are unchanged.

## Code
- `hermes_cli/kanban_completion_refs.py`
- `hermes_cli/kanban_completion_inputs.py`
- `hermes_cli/kanban_origin_ledger.py`
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_completion_refs_sync.py`: `test_a_completion_replayed_before_origins_were_recorded_binds_its_leased_runs_receipt`, `test_a_waived_completion_replayed_before_origins_were_recorded_is_verified`, `test_a_null_origin_completion_without_a_foreign_lease_of_its_run_stays_exact`, `test_a_lease_naming_no_run_uid_of_a_run_without_one_stays_exact`, `test_two_gateways_leasing_one_run_is_ambiguous`, `test_a_pre_uid_replayed_id_naming_another_runs_receipt_still_binds_its_own_run`, `test_a_goal_mode_completion_replayed_from_a_worker_is_verified_there`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The fallback trusts the Board lease as proof of origin; an unsynced board gets no inference and stays fail-closed.
