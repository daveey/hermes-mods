# Update pause: the paused turn's terminal frame passes its wire contract

Patch: 0738 (fork `5a367adffa`).

## Behavior

A turn paused at a step boundary for a core update (see [update-pause-in-place-handoff.md](update-pause-in-place-handoff.md))
ends with a `message.complete` frame that `update_pause.announce_pause` stamps with `paused_for_update: true`. The
`MessageCompletePayload` contract forbade unknown keys and did not declare it, so every paused turn logged
`payload of 'message.complete' violates its wire contract ... extra_forbidden` (in tests the strict contract mode would
have raised).

Now:

- `MessageCompletePayload` declares `paused_for_update: bool | None = None`; the generated TypeScript type
  (`apps/shared/src/gateway-contract.generated.ts`) and OpenRPC file carry it as an optional boolean.
- Every other ending, an ordinary Stop included, leaves the key absent.

## How it works

Contract-only change: the emitter is unchanged. No Desktop code reads the flag yet; it is the terminal-frame signal the
pause feature deliberately sends.

## Code

- `tui_gateway/contracts/events.py`: `MessageCompletePayload.paused_for_update`.
- `apps/shared/src/gateway-contract.*`: regenerated with `scripts/gen_gateway_contracts.py`.

## Tests

- `tests/tui_gateway/test_update_pause.py`: the `turn_env` fixture now runs `check_payload` on every emitted frame (it
  used to replace `_emit` with a bare recorder, which is why the mismatch shipped); new
  `test_announce_pause_frame_satisfies_the_message_complete_contract` (producer output validated) and
  `test_ordinary_stop_is_not_reported_as_a_pause` (no flag, no pause text, no watcher).
- `tests/tui_gateway/contracts/test_generated.py`: `test_message_complete_declares_the_update_pause_flag` (model field
  optional, TS field present) plus the existing byte-for-byte regeneration check.

Before the fix 3 of these failed (the existing paused-turn test, the producer test, the generated-type test); after it,
40 passed across `test_update_pause.py`, `test_update_pause_superseded.py`, `tests/agent/test_step_boundary_pause.py`
and `tests/tui_gateway/contracts/`.

## Applying

Apply after 0737 with `git am -3`. If the generated contract files conflict, take either side and rerun
`scripts/gen_gateway_contracts.py`.

## Gotchas

- The validator error did not stop the continuation: the paused session in the original log continued normally later.
- Tests that stub `server._emit` should still call `contracts.registry.check_payload`, or contract drift stays invisible.
