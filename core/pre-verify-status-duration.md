# Visible pre-verify status and duration

Patches 2072.

## Behavior

Before a registered pre-verify hook judges an already-streamed reply, emit `Checking the reply before finishing…`. Log elapsed monotonic seconds, session, continuation outcome and attempt at INFO after the hook call. Fork turns and turns without a hook stay silent.

## How it works

`_pre_verify_nudge` emits status defensively, starts a monotonic timer and logs in `finally`; a failed status sink does not skip the hook.

## Code

`agent/turn_stop_gates.py`.

## Tests

`tests/agent/test_turn_stop_gates.py`: status precedes the hook, continuation and no-continuation logs, no-hook/fork silence and a failing status sink.

Execution status is recorded in [mirror 2072 verification](mirror-2072-verification.md); listed regression cases alone are not an execution claim.

## Applying

Use the complete snapshot’s [README Applying recipe](README.md#applying), including its replay plan and policy. These patches depend on earlier core changes.

## Gotchas

These are unit tests with a fake status sink and hook. Native Desktop rendering, terminal-tool/execute_code end-to-end behavior and production hook latency are untested here.

## Could this be a plugin?

No standalone plugin is supplied: this changes core dispatch/admission or turn-finalization wiring.
