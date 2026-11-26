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
