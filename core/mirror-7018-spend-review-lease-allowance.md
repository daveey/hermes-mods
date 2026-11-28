# Spend-review lease allowance consumer, decision liveness and budget fences

Patch coverage: 6604, 6699, 6700, 6701, 6702, 6703, 6704, 6705, 6706, 6707, 6708, 6709, 6710.

## Behavior

All of this is live code in the Kanban claim, dispatch-maintenance, release and budget-CLI paths. The Board-allowance parts only act on a **primary synced** board and only for a card whose newest `spend_budget_set` override carries `runs_basis: board_lease_allowance_v1`; on an unsynced board they are inert.

- **Reservation release (6604).** `kanban_spend_reviewer.start_due` hands each reserved in-flight slot to `_start_reserved`. The `STARTED` event append and `Thread.start()` run inside try/finally; if the append (dispatcher write contention, any `BaseException`) or the thread start fails before the target enters, the slot is cancelled and removed from `_INFLIGHT`. A target that already entered (`entered` flag taken under `_LOCK`) keeps custody and its thread is still recorded in `_THREADS`. The target itself checks `cancelled` before running.
- **Allowance classification (`kanban_spend_allowance`, 6699).** `state()` gives `None` (no basis → ordinary cumulative counting), `BOARD` (basis, `runs` exactly integer 10, non-empty `review_uid` and the override event's `uid`, board mode `primary`) or `UNPROVEN` (any other basis shape, or not primary). `kanban_spend_budget.spend()` now carries that state in `Spend.allowance`; `over()` reports `runs` for `unproven`, skips the local run count for `board` (the lease grant counts), and keeps the cache-read limit cumulative. `kanban_terminal_run.reserve` refuses to reserve a last run when an allowance state exists.
- **Claim admission.** `claim_task` and `claim_review_task` add `_spend_allowance.claim_refused(...)` to the same-transaction refusal chain. `claim_gate` sends `expected_reviewer_allowance` (`review_uid`, `budget_event_uid`) in the lease `grant` body; `HubLeaseClient._lease` copies the response's `spend_allowance` onto the `Lease` only when the grant succeeded for this task and this gateway's holder id. `grant_refusal`: no allowance state → refuse only if the lease unexpectedly carried a debit; otherwise admit only when `evidence_ok` (all expected fields equal, `charged is True`, integer `0 <= remaining < 10`); everything else (`unproven`, no lease, missing evidence, old Board) defers the claim with `spend_allowance_unproven` and releases the unused grant. Replica `task_runs` history is never consulted.
- **Observation and decision liveness.** `kanban_allowance_observation.read`/`board_state` do one authenticated `GET /v2/boards/<id>/reviewer-allowance?task=` (new `Hub.reviewer_allowance`, 8 KiB response cap, 5 s deadline), refuse to run inside a transaction, and return unknown (None) on any transport/shape failure. `_remaining` demands exact coordinates and `used + remaining == 10`. `kanban_allowance_maintenance.sweep` is called first in `_start_spend_reviews` (dispatch maintenance): primary boards only, per `(db path, board)` cursor with 60 s cadence, at most 8 candidate cards (`ready`/`review`, no current run, with a Board-basis override) and a 5 s deadline, outside write transactions. A card whose exact allowance is exhausted (`remaining == 0`) or is "stuck" (newest local budget is generic while the Board still holds an allowance) is moved by `_hold` to `triage` in one write transaction after rechecking identity and freshness, with a `spend_budget_set`-family event, a system comment and a decision episode; it skips a card with a pending decision or a foreign copy. 6703 reads the main DB path with `pragma_database_list`.
- **Stale-reading fix (6710).** `stuck()` samples the sync identity and the newest budget **before** the Board read and `still_stuck` compares against them, so a budget or board written while the reply is in flight invalidates the reading; no identity before the read means not stuck. (6700 dropped a redundant in-transaction clause.)
- **Human supersession (6699, `hermes kanban release`).** `_release` calls `_board_allowance` before the budget plan. If any budget of the card named the Board basis, it reads the Board: unreadable → refuse `allowance_unreadable`; none held → continue; held but not `active` → `allowance_invalidated`; human writer with human authority → `supersede`, which makes `_mandate_and_budget` call the validated scope renewal with `allowance_only=True` (a human renewal accepted even with no unrenewed scope restriction, requiring a mandate comment newer than the stop) and report `allowance_superseded`; an agent authority is refused (`allowance_in_force` unless the newest budget is the Board allowance, `allowance_exhausted` at zero remaining) or, with remaining runs, keeps the allowance and leaves the local budget unchanged.
- **Review routing.** On a canonical (primary synced) board `enrich` marks the context `_canonical` and `review` goes through the caused-review path even without a cause; verdict validation rejects a custom `runs` value; `grant_count` ignores caused-stop grants ordered at or before the newest human `scope_renewed`; the local `grant()` returns "synced reviewer grants require the canonical fixed-ten Board operation" whenever the DB is synced.
- **Standalone budget fence (6702, 6704, 6708).** `hermes kanban spend-budget` passes `standalone=True` to `set_override`, which calls `require_decision`. It raises `StopDecisionRequired` (CLI exit 1, error `stop_decision_required`, nothing written) only when the card is in a release-held lane (`_HELD`), the stop is decision-pending, a restricted floor exists, and the restriction is already renewed (`not execution_restricted`). Running/todo/ready cards and unrenewed restricted stops keep ordinary edits; the release composition itself does not set `standalone`.

## How it works

Entry points: `claim_task`/`claim_review_task` → `kanban_spend_allowance.claim_refused`; `kanban_dispatch_maintenance._start_spend_reviews` → `kanban_allowance_maintenance.sweep` then `start_due` → `_start_reserved`; `kanban_release._release` → `_board_allowance`; `kanban_spend_budget_cli.command` → `set_override(standalone=True)`.

## Code

`hermes_cli/kanban_spend_allowance.py`, `kanban_allowance_observation.py`, `kanban_allowance_maintenance.py` (all new), `kanban_spend_reviewer.py`, `kanban_spend_caused_review.py`, `kanban_spend_override_fence.py`, `kanban_spend_budget.py`, `kanban_spend_budget_cli.py`, `kanban_release.py`, `kanban_scope_renewal.py`, `kanban_scope_renewal_cli.py`, `kanban_db.py`, `kanban_db_sync.py`, `kanban_sync_client.py`, `kanban_sync_hub_routes.py`, `kanban_dispatch_maintenance.py`, `kanban_terminal_run.py`.

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. Files: `tests/hermes_cli/test_kanban_allowance_observation.py`, `..._observation_boundaries.py`, `..._maintenance.py`, `..._guard_boundaries.py`, `..._supersession.py`, `test_kanban_spend_allowance.py`, `test_kanban_spend_allowance_board.py`, `test_kanban_spend_allowance_board_review.py`, `test_kanban_spend_caused_review.py`, `test_kanban_spend_review_start.py`, `test_kanban_budget_decision_liveness.py`, `test_kanban_release.py`. Mutant counts and "RED/GREEN" figures in commit text are owner-reported. The two `..._allowance_board*.py` composition files use a real local Board and **skip** unless `B2_M1_LOCAL_BOARD_URL` points at a locally started Board dev server; so the real Board allowance flow is untested here, and everything else runs against fixtures/fakes.

## Applying

Apply in ordinal order; 6702 → 6704 → 6708 successively narrow the fence and 6709 only adds a pin test. 6701, 6705, 6706 and 6709 are test-only.

## Gotchas

- Replica history is never evidence for the Board allowance; observations are diagnostics and never authorize a run.
- A fence that refuses without a release route strands cards; keep it scoped to renewed, decision-pending stops in held lanes.
- Needs a Board that implements `reviewer-allowance` and returns `spend_allowance` on grants; an older Board makes allowance claims defer.

## Why core, not a plugin?

Budget, dispatch and sync are core Kanban.
