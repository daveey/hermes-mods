# Busy pre-swap updates defer without trapping Desktop

Mirror ordinals: **2127–2130**.

## Behavior

The POSIX updater requests an idle-only gateway pause before executable checkout mutations. Busy gateways refuse without starting an uncancellable drain; unsupported older gateways explicitly refuse. The updater defers without moving the executable tree, rather than forcing active work to stop or spending the long post-restart after-turn budget while Desktop cannot reconnect behind the update fence.

Accepted stops are waited for within a bounded pre-swap budget, at most thirty seconds. If a later gateway refuses or an acknowledgment is lost, previously accepted/possible stops are settled before rollback. Exited runtimes can restart; still-live draining incarnations retain durable restart debt rather than being counted as restarted or duplicated. Deferral releases the command's Desktop/update fence.

The legacy non-idle pause remains available and keeps its delayed-callback acknowledgment behavior. This change does not redefine its active-work drain semantics. Test-only ordinal 2130 isolates temporary Git fixtures from the runner's real gateway inventory.

## How it works

`pause-if-idle-for-update` checks the deadline and active work inside the gateway event-loop callback, then preserves cron slots and requests restart in that same callback. Busy or expired callbacks do neither. The client waits past the callback deadline and recognizes an explicit unsupported-verb response. Lost replies remain uncertain, not proof of refusal.

The updater records stop intent before sending requests. Positive busy/unsupported refusal removes restart debt for that PID; uncertain requests retain it. `_stop_gateways` waits for acknowledged/uncertain entries already encountered before reporting the refusal, disarming exited supervised services so they cannot respawn on the old tree during mutation. It no longer escalates the pre-swap wait to forced termination.

The pause gate runs after the local-changes prompt but before stash/discard and other protected checkout-writing steps, even when HEAD would not move. A sticky run verdict fences later commit points. Discovery or durable-record failures also refuse mutation. Partial pause rollback runs while the update fence is still held; resume starts only exited incarnations and merges live drain debt back into the durable token. Nonexecuting lockfile/EOL cleanup before fetch uses an explicitly ungated path; ordinary stash/switch/merge keep the gate. A no-restart run clears any previous in-process verdict.

## Code

- `gateway/control_socket.py`, `run_bootstrap.py`, `cron_pause.py`: idle-only verb, explicit unsupported result, deadline and legacy response compatibility.
- `hermes_cli/update_cmd_posix_pause.py`: bounded pause/refusal, partial settlement, sticky verdict and incarnation-aware resume.
- `hermes_cli/update_cmd_pause_gate.py`, `update_cmd.py`: mutation boundary, recorded bounded deferral and narrow cleanup exception.
- `hermes_cli/update_pause_record.py`: positive-refusal debt removal.
- 2127 introduces the gate; 2128 settles partial stops and reports bounded refusal; 2129 preserves delayed legacy ACK; 2130 changes only test gateway discovery fixtures.

## Tests

Source tests were inspected, **not executed in this task**.

- `tests/gateway/test_update_idle_pause.py`: busy has no cron/restart effects, idle preserves slots before restart, active count is checked inside callback, expired callbacks cannot later drain, real local control-socket routing, legacy active drain and delayed legacy acknowledgment.
- `tests/hermes_cli/test_update_preswap_drain.py`: bounded waits without forced stop, live debt/exited-only restart, interruption/partial rollback under the fence, pause after prompt but before stash/discard with dirty file/HEAD/stash unchanged, discovery/unacknowledged refusal, and a subprocess observing Desktop fence release after deferral.
- `test_update_idle_pause_boundaries.py`: client timeout exceeds callback deadline, unsupported refusal has no debt, a later busy gateway settles an earlier accepted stop, cleanup exception versus real stash gate, and no-restart verdict reset. `test_update_posix_pause.py` checks record failure refusal; `test_update_target_identity.py` adjusts discovery seams.
- 2130 stubs systemd/launchd/bare discovery to empty in `tests/gateway/test_update_streaming.py`, `test_source_channel_integration.py`, `test_source_release_channels.py`, `test_update_channel_release_hold.py` and `test_update_parked_branch_guard.py`. These Git/channel fixtures keep the pause fence real without discovering host services.

No live Mac, launchd deployment, production update or Desktop reconnection proof was collected here.

## Applying

Use [README.md](README.md)'s full snapshot replay and normal supported update/build process. Run the gateway and updater regression modules in an isolated source environment. On an older running gateway that explicitly lacks idle pause, restart it on already-installed code when idle before retrying an update; do not bypass the safety gate by forcing a checkout swap through active work. This documentation made no service changes.

## Gotchas

- A lost acknowledgment may already have initiated an exit. Retain conservative debt until the recorded incarnation settles.
- A successful supervisor `start` while the old PID still drains is not proof of restart.
- The thirty-second cap is the accepted-stop wait, not a promise of thirty-second total update duration.
- Legacy delayed callbacks may still drain and therefore retain `already_stopping`; idle-only expired callbacks must not.
- The lockfile/EOL cleanup exception is narrow, not permission to run arbitrary stash/reset operations before pausing.
- Fixture isolation proves tests do not inventory host services, not that the host has no gateways.

## Could this be a plugin?

No for the safety boundary. Core updater mutations, durable restart records and the gateway's event-loop admission check must agree before files change. Plugins can display deferral/retry guidance but cannot atomically veto every checkout mutation or settle recorded gateway incarnations afterward.
