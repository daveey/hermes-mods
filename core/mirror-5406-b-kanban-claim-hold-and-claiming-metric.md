# Kanban: clear the claim hold when the sync watcher gives up; scoped, expiring `claiming` metric

Mirror entries 5197-5217 (entries 5197, 5198, 5199, 5200, 5201, 5202, 5203, 5216, 5217).

## Behavior

- 5197: when supervision will not restart the sync watcher, the claim hold (`loop_expected`) is cleared, `metrics.claiming=0` is published while the gate is closed, and the machine is dropped from portable eligibility. A `machine:<name>` pin stays.
- 5198-5200: an older Board rejecting `metrics.claiming` with 400 no longer disables counters or strips drain/admission/CPU: claiming is dropped once, then the older chain continues; the rejection is remembered (same window as a rejected counter) so the next report keeps a fresh CPU sample; backoff happens only when the Board's error names claiming, and an unrelated 400 does not hide claiming for 10 minutes.
- 5201-5203: test-only kills for the pinned non-claiming carve-out in `label_machines`, bare-name pickup dropping a non-claiming worker (carve-out needs `machine:<name>` in the wanted set), and the lease-fail and unyielded-return paths clearing the hold.
- 5216: a stale `claiming=0` metric expires on the Board fresh window (90 seconds, milliseconds, with coordination skew), threading one optional `now` through both consumers; unknown or malformed timestamps do not veto; machine pins and bare pickup semantics stay; Board `not_claiming` grants are recorder-only refusals and per-machine placement evidence, not a fleet verdict. 5217 tests that an explicit `now` reaches `_not_claiming` from every caller.

## How it works

- `gateway/kanban_watchers.py`, `gateway/run_startup.py` (give-up wiring); `hermes_cli/kanban_sync_claims.py`, `kanban_move.py`, `kanban_placement.py`, `kanban_capacity_evidence.py`, `kanban_dispatch_skip.py`.
- `hermes_cli/machine_metrics.py`, `machine_drain_metrics.py` (report and backoff).

## Code

- `gateway/kanban_watchers.py`
- `gateway/run_startup.py`
- `hermes_cli/kanban_sync_claims.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/machine_drain_metrics.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/kanban_capacity_evidence.py`
- `hermes_cli/kanban_dispatch_skip.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_claim_gate_giveup.py`, `test_machine_metrics.py`, `test_machine_drain_metrics.py`, `test_claiming_freshness.py`, `test_not_claiming_skip.py`, `tests/gateway/test_kanban_sync_watcher_claim_fence.py` (`test_failed_lease_acquire_clears_the_claim_hold`, `test_unyielded_sync_return_clears_the_claim_hold`, `test_sync_disabled_watcher_leaves_claims_open`). A real Board rejecting the field is simulated only.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The 600 s backoff window applies only to claiming-named rejections.
- Reason strings are recorder-only; do not add them to expected-hold sets.

## Why core instead of a plugin

Dispatcher claim gating and the machine metrics report are core kanban/gateway behavior that must agree with the Board's claim grant.
