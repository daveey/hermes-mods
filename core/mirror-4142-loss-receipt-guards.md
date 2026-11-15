# Loss receipt guards and quota start results

Mirror snapshot 4142; ordinals 4060, 4095.

## Behavior

- 4060 (tests only): when a worker loss is recorded and the worker binding, birth time, PID or claim lock is missing or does not match, the `worker_lost` diagnostic event is still written but carries no `loss_receipt` or `death_proof_version`. If a faulty writer did attest such a loss, the consumer still keeps the task running.
- 4095: when a worker already recorded a non-continuable failed interpreter result (for example a startup refusal with quota exit code 75), a later owner-side loss echo no longer overwrites it with an unknown (`exit_code: None`) loss receipt. The loss event stays as a diagnostic. The next dispatcher tick can then release the claim, keep the failure count and hold the task until the quota reset time instead of treating the run as lost.
- Clean, malformed, continuable, owner-restart and rebound evidence stays on the existing witnessed-loss path.

## How it works

- 4060: Adds `test_kanban_loss_emission_guards.py`, which damages the stored evidence before calling `record_worker_loss`, and a second group that replaces `loss_identity_fields` with an unchecked producer to show the consumer (`detect_crashed_workers`, both normal and `failed_results_only` modes) holds on its own. `lost_after_restart` in the rebound-loss test gains an `emit_loss` keyword so the helper can stop before emission. No production code changes.
- 4095: `loss_identity_fields` in `kanban_lost_result.py`, after its existing PID and claim checks, looks up the modern receipt for the run. If the selected result has an integer, non-zero `exit_code`, is not `continuable`, and has no `result_role`, it returns `{}` so no loss receipt is attached.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_lost_result.py`
- `tests/hermes_cli/test_kanban_loss_emission_guards.py`
- `tests/hermes_cli/test_kanban_rebound_worker_loss.py`
- `tests/hermes_cli/test_kanban_quota_start_loss.py`

Mail entries in the ordered series:

- 4060: [`435e55ceeb3b7d7338f5b4e9eada8ccd7e7fa980`](patches/4060-daveey-Pin-loss-receipt-writer-guards-before-emissio.patch)
- 4095: [`89a90ff35bedb661e397f752334abc418e8de89a`](patches/4095-fix-kanban-preserve-quota-startup-result-across-owne.patch)

## Tests

`tests/hermes_cli/test_kanban_loss_emission_guards.py` (4060):

- `test_unverified_loss_preserves_diagnostic_without_attesting_receipt`: for `missing_binding`, `missing_birth`, `wrong_pid`, `wrong_claim`, exactly one `worker_lost` event exists with the reason and PID and without `loss_receipt` or `death_proof_version`.
- `test_consumer_holds_even_if_writer_attests_invalid_loss`: with an injected faulty writer, for `missing_birth`, `wrong_pid`, `wrong_claim` and both observer modes, the failed-result proof is None and the task stays `running` with its claim, run and zero failures.

`tests/hermes_cli/test_kanban_rebound_worker_loss.py` (4060): helper `lost_after_restart` gained `emit_loss`; no new test function.

`tests/hermes_cli/test_kanban_quota_start_loss.py` (4095):

- `test_quota_unknown_owner_releases_in_one_tick`: a child subprocess exits 75 with a typed quota receipt, the owner records loss, the submitter processes an unknown result; after one `detect_crashed_workers` the task is no longer running, has no claim, keeps `consecutive_failures == 1`, is not in triage, is held until at least the reset time, and no `failure_finalized` event exists.
- `test_loss_witness_preserves_only_final_interpreter_failure`: for `none`, `zero`, `bool`, `continuable`, `owner_restart`, `same_pid_rebound`, a loss receipt is attached for every case except `none`.

The paths exercised are the in-process gateway loss recorder, a native subprocess and the dispatcher; no REST or Desktop path is tested (untested).

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- 4095 keeps the startup cause; it does not change how a remote Board server handles lease expiry, and the commit message says that recovery is owned separately.
- It does not permit reclaiming a live interpreter; existing run and process-death checks still control closure.
- Only results with an integer non-zero exit code, not continuable and without a result role are preserved.
- The 4060 tests state that masking by the consumer is not a claim that the writer guards are redundant.
- This document makes no deployment or live-behavior claim.

## Plugin?

No. These are core worker-loss and failed-result contracts; a plugin cannot change what the loss recorder attests.
