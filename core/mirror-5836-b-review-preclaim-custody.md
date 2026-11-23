# Review-owned preclaim refusal and accounting

Source patch ordinals: 5572–5577, 5579.

## Behavior

Structural review-routing refusals happen before claims or runs consume retry/failure budgets. Review-specific temporary walls remain owned by review admission, rather than the generic ready-class gate. Repeated home-owned structural refusals can become a visible transient triage block.

## How it works

`hold` catches `ReviewModelUnavailable`, records `review_model_unavailable`, and avoids run allocation. `record_refusal` deduplicates matching error/source-run observations within the cooldown and tracks bounded deferrals; exhausted refusals append `gave_up` and move only an unclaimed ready/review card. Foreign copies refuse locally without owning escalation. Dry-run admission is read-only. Fixture updates supply actual source completion and per-profile class registries to reach the intended gate.

## Code

- `hermes_cli/kanban_review_admission.py`
- `hermes_cli/kanban_class_walls.py`
- `hermes_cli/kanban_dispatch_skip.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_review_preclaim_refusal.py` covers read-only dry runs, cooldown, foreign custody and candidate walls. `test_kanban_managed_pin_policy.py` checks review-owned preclaim behavior and bounded visible blocking. `test_kanban_source_receipt_sites.py` checks running-source custody cannot be ended. `test_kanban_review_model_cycles.py`, `test_kanban_review_wall_admission.py` and `test_kanban_shared_blockers.py` compose no-run refusal with dynamic allocations and healthy-profile recovery; class/tier fixture consumers are aligned.

## Applying

Apply after model-neutral routing and with review admission consumers. Later review semantics and capability checks refine eligibility; preserve this family's preclaim accounting and custody fences. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

An admission refusal is not an execution and does not reset ready age. Only ready/review lanes may be transitioned by this recorder; a running source retains finalizer custody. Historical tests mentioning model diversity must be read with the later separate-execution retirement.

## plugin?

No. Plugins may display refusals but cannot own claim or source-run finalization.
