# `hermes serve` waits out a core update before loading the agent

Patch: [`patches/0697-fix-serve-wait-out-a-core-update-or-the-admission-ho.patch`](patches/0697-fix-serve-wait-out-a-core-update-or-the-admission-ho.patch)

## Why

A backend that boots while `hermes update` sits between its drain and its checkout imports the old tree. It keeps
serving that code until the code-skew watchdog (`hermes_cli/web_server_skew_exit.py`) retires it, and the
watchdog only does that between turns. In the case that prompted this, a Desktop on another Mac respawned its
SSH backend at 00:51:23. hermes-sync's admission hold had been up since 00:51:19, `hermes update` wrote its marker
at 00:51:25, and the checkout moved at 00:51:44. The backend had finished importing by 00:51:31 and served
pre-update code for 41 minutes. Desktop's own spawn gate checks only the marker, so it let the spawn through.

## Behavior

`cmd_dashboard` runs `hermes_cli/serve_update_wait.py::wait_for_update_clear()` for headless `serve` only.
It runs after profile routing and the nofile limit, and before the one-shot SSH token is read, the runtime is
prepared and `hermes_cli.web_server` is imported. The gate waits on two things:

- **A live update**: `update_lock.read_live_update()` or a held checkout lock. The wait is bounded at 600 s, the
  same as the always-on dashboard's `UPDATE_WAIT_S`. A Desktop client that gives up waiting for READY (120 s)
  kills the child, and its marker gate then parks the respawn, so nothing wedges.
- **hermes-sync's admission hold** (`<root>/core-update/holds/hermes-sync`, via `admission_fence.is_held`). A hold
  alone does not prove an update is coming, because a drain window holds it while turns finish. So it is waited
  out for at most 60 s. If an update starts during that wait, the 600 s update bound applies instead. Once that
  update has released its marker, a hold that is still up does not extend the wait.

Every wait logs to stderr, which is the serve log. It logs `waiting …` when it starts, then `clear after Ns` or
`… starting anyway` when it ends. Both bounds fail open, and the skew watchdog still retires the backend later.
The update that spawned this serve is exempt: a marker holder that is this pid or an ancestor, or an inherited
checkout-lock fd. Knobs: `HERMES_SERVE_UPDATE_WAIT_S` (`0` turns the gate off), `HERMES_SERVE_HOLD_WAIT_S`
and `HERMES_SERVE_UPDATE_POLL_S`.

## Tests

`tests/hermes_cli/test_serve_update_wait.py`:

- Through `cmd_dashboard`, a serve started while an update is live, while a real hold file exists, or while a
  real `UpdateLock` is held by another process does not start serving or read its token until the wait clears.
- With nothing pending, the serve starts at once and never sleeps.
- Policy: the bound and its log lines, the shorter hold bound, a hold followed by an update (the update bound
  applies and the trailing hold is ignored), `0` turning the gate off, and the exemption for our own updater.
