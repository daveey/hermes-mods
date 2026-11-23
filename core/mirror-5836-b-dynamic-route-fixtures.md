# Consumer fixtures for dynamic class admission

Source patch ordinals: 5582–5583, 5586, 5588–5594, 5596–5601, 5603–5606, 5608–5612.

## Behavior

Test-only consumer migration keeps existing custody, capacity, pacing, lifecycle and billing assertions meaningful under model-neutral routing. Fixtures provide real eligible profile registries and authentic completed review sources instead of relying on historical concrete pins to reach the tested gate.

## How it works

Move/remote-assignee fixtures configure each gateway/profile explicitly. Shared review helpers reconstruct source claims, run receipts and completion before testing downstream admission. Loopback gateway fixtures use dynamic class routes. Capacity, memory, spend, drain, sync, quiet, fairness and quota cases retain their target controls while supplying eligibility prerequisites. Test helper extraction keeps isolated profile diagnostics reusable; YAML policy composition avoids duplicate top-level keys.

## Code

- `tests/hermes_cli/kanban_review_fixture.py`
- `tests/hermes_cli/kanban_guard_helpers.py`
- `tests/hermes_cli/kanban_sync_support.py`
- `tests/hermes_cli/hub_admission_support.py`
- `tests/gateway/fixtures/kanban_loopback_routing.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

Source coverage includes `tests/hermes_cli/test_kanban_move.py` and `test_kanban_remote_assignee_entry_paths.py` (remote custody); `test_kanban_capsule_claim_race.py`/`test_kanban_dispatch_claim_window.py` (claim rechecks); `test_kanban_host_cap.py`, `test_kanban_capacity_order.py`, `test_kanban_memory_guard.py` and `test_kanban_per_profile_cap.py` (capacity); spend/park/stuck-clock suites; agent/gateway/core drain suites; sync-claim and quiet-gate suites; quota gateway/guard and fairness/hold suites. `tests/gateway/test_session_kanban.py` and owner/recovery/restart probes retain lifecycle custody. `test_welcome_trial.py` checks a trial cannot bypass a missing class registry; `tests/plugins/test_kanban_model_override.py` provisions the executing profile for tier admission. These are fixture/expectation changes, not newly added production entry paths.

## Applying

Apply alongside the model-neutral and preclaim-routing series. To reproduce later, use the source repository's supported runner and isolated fixtures; no live gateway/profile configuration is a test prerequisite. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

A test expecting a later gate must first establish eligible routing and authentic review custody. Creating only a review-shaped row or configuring only the board default can cause an earlier refusal and a false interpretation. These patches do not relax production safety controls.

## plugin?

No new plugin capability. Some suites exercise existing plugin/gateway consumers, but this group changes tests and helpers only.
