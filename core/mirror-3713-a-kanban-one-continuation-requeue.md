# Kanban: core-update requeue keeps one-continuation grants

Patches: **3691–3692**.

## Behavior
- An operator resume grants an `active_pr`-held card one continuation. If a restart-safe core-update requeue reclaims that continuation run, the grant is no longer spent; the card is not held again with no work done.
- The grant is spent by the next real claim, or by a crash/timeout/gave_up/spawn_failed event, or by a reclaim with any other reason (fail closed).
- A resume recorded while the newest run is a requeued run is honoured.

## How it works
3691: `resumed_after()` compared the receipt's `after_run` with the raw newest run row, so a requeue counted as consuming it. It now walks runs newest first, passing over core-update requeues and attested unused grants (the same view other one-shot handoffs use), and the receipt holds while the named run is reached before any real run.
3692: adds one cross-path test running the same grant, claim, requeue, requeue, real-claim fixture through the dependency handoff, owner-wake hold release and operator resume paths, plus fail-closed reclaims, so a new path that checks the raw latest run fails.

## Code
- `hermes_cli/kanban_operator_resume.py`
- `tests/hermes_cli/test_kanban_operator_resume_requeue.py`
- `tests/hermes_cli/test_kanban_one_continuation_requeue.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_operator_resume_requeue.py`: `test_core_update_requeue_keeps_the_receipt_until_the_next_real_run`, `test_resume_recorded_while_newest_run_is_requeued_is_honoured`, `test_crash_or_other_reclaim_spends_the_receipt`.
- `tests/hermes_cli/test_kanban_one_continuation_requeue.py`: `test_requeue_is_a_pause_and_the_next_real_claim_spends_the_grant`, `test_any_other_reclaim_spends_the_grant`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3692 is test-only and relies on the 3691 fix.
