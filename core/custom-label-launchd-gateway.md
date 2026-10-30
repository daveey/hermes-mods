# Discover custom-label launchd gateway supervisors and hand back ownership

Patches: 1154 1193 1194 1195.

## Behavior

- When no canonical job exists, a loaded, enabled KeepAlive LaunchAgent serving the exact resolved `HERMES_HOME` through `gateway run` is the home’s service, even under a custom label. A launcher’s `--home` must also match.
- Ensure uses plain `launchctl kickstart` for a stopped discovered job, never `-k` and never an unmanaged gateway beside it. A running custom job is awaited, not restarted.
- `gateway restart --detach` hands the home to a discovered service instead of spawning an unmanaged replacement. Without any service, unmanaged replacement remains available.
- A `--external-supervisor` start refused by a live owner identifying as `manual` writes that owner’s planned-stop marker and still exits 1. The supervisor’s next restart can take over after graceful drain; supervised, Desktop and silent owners are not asked to stop.
- (1193) Under a supervisor, a `gateway run` that would ATTACH to a live gateway launched from THIS home records `gateway.start_refused` (`already_running`, detail `host_attach`, `external_supervisor`) in `gateway-exit-diag.log` and asks a MANUAL owner to drain with a planned-stop marker. Exit code is unchanged (75). An attach to another home's multiplexer, and an unsupervised `gateway run`, are untouched.
- (1194) `hermes update`'s `_prepare_profile_gateway_update_restart` returns `external-supervisor` and arms no detached relaunch for a manual gateway of a home a KeepAlive launchd job serves; the job respawns it. With no such job, or on a probe error, the detached relaunch is kept.

## How it works

Fallback discovery scans `~/Library/LaunchAgents`, validates the plist home and argv, and confirms the job is loaded through launchctl. Home comparison resolves symlinks but does not merge separate profile homes. Detached replacement reuses service discovery. Gateway bootstrap requests manual-owner handback through its existing planned-stop mechanism.

1193 adds a step in the CLI host-attach guard (`_attach_to_host_gateway_or_guard`): for a supervised start whose ATTACH owner's home resolves to this home, it calls `note_start_refused`, `_hand_home_back_to_supervisor(owner.pid)` and writes the exit-diag row; failures are logged at debug and the attach exit stands. Before it, once the manual owner had published its host record, KeepAlive retries stopped in this guard ("nothing to start", exit 75) and never reached the duplicate guard, so there was no handback and no diag row. 1194 calls `custom_launchd_supervisor_for_home` (10 s budget) before arming a relaunch. 1195 moves both helpers out of `hermes_cli/gateway.py` (line-cap health check) into `hermes_cli/gateway_supervised_handback.py` as `supervised_attach_exit_code`, `custom_job_serves` and `_host_decision_exit_code`; behavior unchanged.

## Code

- `hermes_cli/gateway_runtime_service.py`
- `hermes_cli/gateway_runtime_start.py`
- `gateway/run_bootstrap.py`
- `hermes_cli/gateway.py` (1193, 1194; 1195 reduces these to calls)
- `hermes_cli/gateway_supervised_handback.py` (1195)

## Tests

Source regression coverage:

- `tests/hermes_cli/test_gateway_runtime_custom_launchd.py`
- `tests/hermes_cli/test_gateway_start_refused.py`
  - 1193: `test_supervised_attach_to_owner_of_this_home_is_a_recorded_refusal_with_handback` (owner `manual`: marker written and `handback` recorded; `launchd` and unknown: recorded, no marker), `test_supervised_attach_to_a_host_gateway_of_another_home_is_not_a_refusal`, `test_unsupervised_attach_stays_a_clean_attach`.
- `tests/hermes_cli/test_gateway_runtime_custom_launchd.py` (1194): `test_update_hands_a_manual_gateway_of_a_supervised_home_back_to_its_job`, `test_update_still_relaunches_a_manual_gateway_when_no_job_serves_the_home`. The probe-exception fallback to relaunch is untested.
- 1195 adds no tests; the 1193/1194 tests exercise the moved helpers.

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

The discovery, ensure, detached-restart and handback matrix tests stub launchctl/plists and owner responses. They run on Linux but do not exercise a real macOS LaunchAgent. Disabled, unloaded, non-KeepAlive, wrong-home, wrong-launcher-home and non-gateway jobs are rejected. Handback is not forced termination.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
