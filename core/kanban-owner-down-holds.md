# Kanban profile-owner-down holds

Patch **1678** (`592b81d23adb913f919a4c0dfafae7ff61fe4793`).

## Behavior

A worker refused before admission because its owner's service is failed, masked or disabled exits with `KANBAN_OWNER_DOWN_EXIT_CODE` (68), rather than charging the card an ordinary crash. The dead-worker sweep records `owner_down` and requeues without consuming the failure budget when the existing no-admission/neutral-streak checks permit it.

Before claiming a non-default profile's card on Linux, dispatch probes the owner's systemd service read-only. A persistent refusal parks the card as `scheduled`, without a claim/run, with an `owner_down:<profile>:<code>` reason and a unit-state wake check. One open owner-down card per profile/code is reused; subsequent holds comment on it. When another open card names the unit, the repair card depends on that work rather than restarting an intentionally paused service.

## How it works

`kanban_owner_down.probe()` caches per-profile results for 30 seconds and bounds the service probe to five seconds. Default profiles, non-Linux hosts and unknown states retain their existing path. `preflight()` runs before `claim_if_admitting`; non-SQLite probe/parking errors log and fall back, while SQLite errors propagate.

The worker's `_owner_down()` requires `GatewayUnavailableError`, a persistent reason code and no admission ID. `_submit()` maps that classification to 68. Dispatcher exit classification and `_DeadWorker.run_outcome` preserve `owner_down`; `kanban_owner_unavailable.note()` shares the existing turn-free proof and bounded neutral streak with owner-unavailable instead of granting unlimited free retries.

`ensure_card()` searches for an open repair card, otherwise creates it on the default profile with an optional parent matching the service unit. `hold()` records the repair card in `awaiting` and schedules a shell probe that reads `ActiveState`/`UnitFileState`; it never starts the unit.

## Code

New `hermes_cli/kanban_owner_down.py` and `kanban_db_dispatch_preclaim.py`; integration in `gateway_client.py`, `kanban_worker_client.py`, `kanban_owner_unavailable.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_dead_worker.py`, `kanban_db_dispatch_exit_code.py`. The preclaim module also extracts existing deduplicated missing-profile diagnostics.

## Tests

`tests/hermes_cli/test_kanban_owner_down.py`:

- `test_worker_exits_owner_down_on_a_failed_service`
- `test_worker_exits_owner_down_on_masked_or_disabled`
- `test_owner_down_service_failed_parks_uncounted`
- `test_preflight_skips_claim_for_failed_owner`
- `test_owner_down_dedup_card`
- `test_unknown_refusal_code_keeps_todays_path`
- `test_wake_check_command_reads_unit_state`

These source tests were not rerun in this mirror. They use controlled service/worker fixtures; no live systemd repair or deployed multi-profile recovery is claimed.

## Applying

Apply 1678 in the [ordered mirror replay](README.md), after the existing owner-unavailable admission accounting and gateway service-probe machinery. No service activation or config change is performed by mirroring it; this is core dispatch logic, not a plugin.

## Gotchas

A post-admission failure is not automatically neutral. The shared neutral-streak bound still applies. The wake shell explicitly blocks `failed` and `masked` and returns 2 when systemctl cannot answer; it does **not** independently test `disabled`, despite disabled being a persistent classification at preflight. Do not interpret a passed wake check as proof the service is running: dispatch probes again. An intentionally paused unit must remain paused until its owning work permits recovery.
