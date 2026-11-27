# hub-status

Backend half of the **Hub** status-bar chip (`desktop-plugins/hub-status`).

- `GET /api/plugins/hub-status/status` → `{state: up|restarting|down, reason, since, now, pid, gateway_state}`.
  `since` is the gateway process start when up, else the start of the current restart/outage.
- `GET /api/plugins/hub-status/events?range=24h|7d|30d` → transitions in the window, led by the state in
  force at its start (`clipped: true`).

State rules (`core.classify` / `core.escalate`):

| gateway_state.json / systemd | state |
|---|---|
| running, degraded and the recorded pid is alive | up |
| systemd activating/deactivating; starting; draining; stopped with `restart_requested` | restarting |
| restarting for more than 5 min | down (`restart did not finish within 5 min`) |
| running but pid gone; stopped/startup_failed without restart; no state file | down |

The Desktop chip adds a fourth case itself: the backend can't be reached at all (two failed polls) → red
"unreachable", timed from the last good poll.

History: one sampler thread per `HERMES_HOME` (flock on `hub-status/sampler.lock`, so only one of
several dashboard backends writes) samples every 10 s and appends only transitions to
`$HERMES_HOME/hub-status/events.jsonl`; a heartbeat gap over 60 s at sampler start is logged as a
`down` span ("hub offline"). Kept 30 days.

## Always-on sampling without Desktop (Linux)

Router loading is lazy: **the REST fallback alone does not log anything until the first authenticated
plugin request**. Install the independent user service on the monitored host so history is captured
whether or not Desktop has connected:

```sh
python3 ~/.hermes/plugins/hub-status/install_sampler.py
systemctl --user status hub-status-sampler.service
```

The installer uses the invoking Python (stdlib only), the plugin's resolved directory, and
`$HERMES_HOME` (default `~/.hermes`). Explicit homes: `--home /path/to/hermes-home`; interpreter:
`--python /path/to/python3`. It writes under `$XDG_CONFIG_HOME/systemd/user` (default
`~/.config/systemd/user`), reloads systemd, enables and restarts the service. Re-run after moving the
plugin or interpreter. This single named unit monitors one home; use separately named units for
additional profile homes. User services require a running user manager (and linger for boot without login).

The service runs `python -m core --home ...` every 10 seconds under the **same flock** as REST sampling.
It remains alive and retries if a dashboard already holds the lock; after that dashboard exits it takes
over within 10 seconds without a request. It has no lifecycle dependency on the gateway or dashboard,
so a gateway restart cannot kill its observer and produce a false monitor-offline gap. Existing restart
classification and five-minute grace are unchanged. No agent `register` hook starts a sampler.

Read `$HERMES_HOME/hub-status/events.jsonl` and `heartbeat` to verify recording without calling an API.
The API's `sampler` flag describes only that backend's local thread; it can be false when the service
owns sampling. Disable with `systemctl --user disable --now hub-status-sampler.service` to return to
lazy REST-only sampling.

Tests: `scripts/test-all --no-core -k hub-status` (backend plus Desktop). The subprocess regression
uses a stubbed home and actual 10-second cadence, terminates the old dashboard sampler, and verifies
`up → restarting → up` in under 30 seconds per transition with no router import or HTTP request.
Separate process contenders prove the flock has exactly one winner and is released on owner exit.
