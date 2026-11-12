# Warm Linux always-on dashboard rollover and custody

Patches: **2881, 2882, 2883, 2884**.

## Behavior

Linux always-on dashboards use a stable loopback TCP proxy plus warmed blue/green backend units, rather than killing the serving backend before its replacement boots. New connections follow the active backend record; existing connections stay with their original backend. Each backend has its own verifiable custody record.

## How it works

- **2881:** install proxy, backend template and roller systemd user units. The roller boots/adopts a free backend, probes health, replays allowlisted read-only GETs in two warm rounds and atomically switches `active.json`. Update refresh routes managed backend PIDs to one warm-roll request instead of killing them. Backends retire through their idle fence after supersession, not on checkout skew. macOS keeps the prior launchd path.
- **2882:** preserve responses to half-closed requests; invalidate proxy target cache by inode+mtime. Adopt an interrupted roll’s still-booting backend and recheck health before switch. Supervision survives bad polls. Persist superseded age; after 900 seconds retire through prepare+commit idle fence, with SIGTERM only at the six-hour hard bound. Superseded watchdog polls every two seconds (ten after a busy refusal). Paused turns wait up to 420 seconds for a reported warming successor rather than using the normal 90-second handoff deadline; busy backends still use their existing early-continue rule.
- **2883:** before backend exec, write private `backends/<port>.json` with PID/start time, port, unit, ownership ID and nonce. `verify_backend_custody` checks cgroup-derived port, current kernel start time, matching persisted nonce/ownership and `--ssh-owner-nonce` argv.
- **2884:** pin UTF-8/replacement decoding on roll-request subprocess output and exempt the roller’s authenticated loopback backend client from the static route-guard scan in its test.

## Code

- `hermes_cli/always_on_dashboard.py`
- `hermes_cli/always_on_rollover.py`
- `hermes_cli/dashboard_procs.py`
- `hermes_cli/web_server.py`
- `hermes_cli/web_server_skew_exit.py`
- `tui_gateway/update_pause.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_always_on_rollover.py` covers real loopback proxy traffic/half-close, atomic target changes, fake-systemctl roll/adoption/failure, allowlisted warming, unit rendering, backend exec, custody mismatch cases and fenced retirement. `test_update_pause_recovery.py` checks warming-successor grace and busy/no-switch controls. `test_always_on_dashboard.py` and `test_dashboard_session_token_routes.py` adapt integration/scanner expectations.

- `tests/hermes_cli/test_always_on_dashboard.py`
- `tests/hermes_cli/test_always_on_rollover.py`
- `tests/tui_gateway/test_update_pause_recovery.py`
- `tests/hermes_cli/test_dashboard_session_token_routes.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

After replay, Linux needs the supported `hermes always-on-dashboard install` operation to write/load the new unit topology; perform that as a separately authorized deployment, not as part of this documentation pass.

## Gotchas

This pass does not install units, run a live systemd rollout or measure live reconnect latency. Health-probed warmup is bounded/best effort, not proof every route is hot. Exhausted backend ports defer a roll. The proxy closes clients if no upstream answers within its hold bound. A custody JSON alone is not authority: live PID identity and nonce must agree.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
