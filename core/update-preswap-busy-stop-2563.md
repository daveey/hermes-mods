# Opted-in pre-swap stop: update over busy gateways within one deadline

Patches: **2542–2553**.

## Behavior

The pre-swap pause of `hermes update` is idle-only: a gateway with active work, or one too old to know the idle verb (for example a pinned gateway), defers the update, so a host that is never idle could never converge. An automated caller that has already drained what it is willing to wait for can now set `HERMES_UPDATE_PRESWAP_STOP_BUSY=1` (`1`, `true` or `yes`):

- Every in-scope gateway gets the ordinary drain request (older gateways answer it; in-flight turns keep their resume markers), then the survivors are stopped through their supervisor (bare gateways are killed) before the checkout moves.
- **Linux only.** On macOS the variable is ignored (a notice is printed when it was requested) and the pause stays idle-only.
- The whole stop is one absolute 30 s transaction that starts before gateway discovery: discovery subprocesses, control-socket exchanges, drain waits, supervisor stops and verification all spend it. Nothing is dispatched once it has passed.
- It fails closed. Any gateway that cannot be listed, whose ownership or supervisor cannot be proven, that cannot be managed, or that is not verified stopped (process gone; unit inactive with no job; launchd job without a pid) refuses the pause with "the checkout was not changed", and the existing durable record rolls back. The rollback restart is announced as a separate recovery phase, not bounded by the 30 s.
- Gateways proven foreign (home outside the update scope) or pinned to another checkout are left alone and do not refuse.
- Repeated refusals are attributed per gateway and reported through the deferral streak (third consecutive attempt).
- Without the variable nothing changes, and the variable is not passed to replayed gateways.

## How it works

`hermes_cli/update_cmd_posix_pause.py`: `_stop_busy_opted_in()` (env flag and `_is_linux()`), `_pause` starting the deadline before discovery, and `_stop_busy_gateways`, which sends `_ask_busy_to_drain` (the ordinary `pause_gateway_for_update`, bounded by the deadline), waits once for all, reserves the last seconds for stops (`_STOP_RESERVE_S`), queues stops without blocking (`systemctl stop --no-block`, a background `launchctl bootout`, SIGTERM for bare), force-kills survivors through the supervisor, and verifies with `_stopped`. `_subprocess_budget` clamps every discovery `subprocess.run` of the thread; any timeout means an unfinished listing, never an empty one. `gateway/control_socket.py` `_query_unix_socket` uses one deadline for connect, send and every read, so a trickling peer cannot stretch it.

Strict inventory lives in the new `hermes_cli/update_preswap_discovery.py`: `systemd_units()` refuses on unreadable listings, MainPID, ActiveState or ownership (no implicit default-home inference, via `systemd_unit_hermes_home(..., allow_default=False)`), and `bare_gateways()` considers only transaction-owned service PIDs. `_bare_supervisor_evidence` returns proven-bare / unmanaged supervisor / unreadable (any non-managed `.service` cgroup, `--external-supervisor`, s6 environment, or unreadable cgroup/environment refuses). Best-effort fleet helpers keep their default behavior. See [core-update-capped-pause.md](core-update-capped-pause.md), [update-idle-pause-deferral-2131.md](update-idle-pause-deferral-2131.md) and [update-external-supervisor.md](update-external-supervisor.md).

## Code

`hermes_cli/update_cmd_posix_pause.py`, `hermes_cli/update_preswap_discovery.py` (new), `hermes_cli/update_fleet_scope.py`, `gateway/control_socket.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests). They call `pause._pause`, `_stop_gateways`, `commit.arm_commit_point` and `control_socket._query_unix_socket` directly with fake `systemctl`/`launchctl` executables, synthetic probes and real helper processes; the `hermes update` CLI command end to end and a real chat `/update` flow with the variable set are untested:

- `tests/hermes_cli/test_update_preswap_drain.py`: busy gateway stopped within 30 s before any tree move; no opt-in defers; pinned-verb gateway; failed stop refuses; variable not inherited by replay; macOS ignore (native macOS lane).
- `tests/hermes_cli/test_update_preswap_deadline.py` (Linux): single deadline, hung stops/RPCs/bootout, many entries, expired deadline, discovery clamp, unmanageable gateways, recovery phase.
- `tests/hermes_cli/test_update_preswap_strict_discovery.py`, `test_update_preswap_bare_discovery.py`: strict systemd and bare inventory, supervisor evidence, streak reporting.
- `tests/hermes_cli/test_update_pause_pinned_runtime.py`, `test_update_posix_pause.py`, `test_update_fenced_launcher_pause.py`: pinned units untouched, updated call signature, fence custody.
- `tests/gateway/test_control_socket_pause.py`: single-deadline exchange with a trickling peer.
- `tests/gateway/test_update_command_active_work.py`: a chat `/update` command does not count as active work.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Opt-in only and Linux only; set it from automated callers, not interactive updates.
- `_stop_gateways` and `_ask_busy_to_drain` take a deadline/timeout; out-of-tree callers must pass it.
- Opted-in discovery is stricter than the default: unknown ownership or supervisor now refuses where the idle-only path would continue.
- The 30 s bound excludes the rollback recovery after a refusal.

## Plugin

Core change; no plugin required.
