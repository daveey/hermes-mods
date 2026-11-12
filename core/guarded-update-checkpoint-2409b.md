# Guarded instant-update checkpoints

Patches: **2342–2347, 2352–2353**.

## Behavior

With a valid explicit instant-update intent, the updater asks the gateway for a checkpoint: known managed cards and exactly published cron reservations may cross the pause, while unknown work or an unpublished reservation is refused without a stop. Without a valid intent only the stricter idle request is used, and a gateway lacking the checkpoint verb gets only that. ACKs are believed only from the gateway pid asked and for the right nonce. Acknowledged drains and unknown-outcome settlement are bounded at 70 s (default budget stays 30 s) and settlement never authorizes mutating the checkout. A withdrawal after admission cancels the restart rather than stopping (2344). A launchd PID that is only a launcher parked at the updater's ancestor fence is exempt from the pause wait (2352).

## How it works

`gateway/update_checkpoint.py` runs the request on the gateway loop without waiting on it and claims permission atomically versus the handler timeout; a claimed-but-incomplete request is unknown, not refusal. Cron's lock is read without holding the admission lock (reverse order). `admission_fence.state(..., details=)` exposes details; `update_cmd_posix_pause.py` validates ACKs and budgets. `parked_launchd_launcher` in `update_gateway_fence.py` proves identity from exact argv (direct executable or interpreter+script, never a substring in a shim) and fails closed on ambiguity, missing home or missing psutil. 2347 routes the checkpoint module through the upgrade CI classifier.

## Code

`gateway/update_checkpoint.py`, `gateway/run_bootstrap.py`, `gateway/run_shutdown.py`, `hermes_cli/admission_fence.py`, `hermes_cli/update_cmd_posix_pause.py`, `hermes_cli/update_gateway_fence.py`, `scripts/ci/classify_changes.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_update_checkpoint.py`, `tests/hermes_cli/test_update_checkpoint_pause.py`, `tests/cron/test_update_checkpoint_tick_handoff.py` (real cron tick reservation handoff), `tests/hermes_cli/test_update_fenced_launcher_pause.py` and `test_update_posix_pause.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Real launchd and gateway behavior on a live machine is not exercised by these tests. An ACK extends only the wait, never authority to move the checkout. Launcher exemption applies only to a positively identified parked launcher; a live answering gateway still waits.

## Plugin

Core change; no plugin required.
