# Update pause re-queues a notifier wake claimed before the hold

Patches: **3514**.

## Behavior
- During a restart-safe update hold, a notifier wake (a folded task-update batch) that was claimed before the hold but only begins executing inside it no longer starts. It ends unstarted (no request, nothing persisted, transcript unchanged) and goes back to the head of its FIFO until the hold lifts.
- Human turns and checkpoint resumes keep their existing behavior under the hold.
- A pending user Stop or steer wins over the gate.
- If the re-queue is refused, the existing checkpoint path is used.

## How it works
3514 extends `pause_before_turn` (runs after the turn lease is acquired, before the turn body and its preflight compression) to consult a per-run gate, `update_yield_before_turn`. A truthy reason ends the turn unstarted and the result carries `update_pause_unstarted=True`. The ingress and hook modules set that gate for a fresh claim of the claim-held class, using the claim's own hold test, and remove it afterwards. `project_paused_result` passes the marker through. `requeue_unstarted_input` moves a row started -> queued for the exact owner epoch and generation; it refuses once the conversation transcript has grown past the claim's mark, or a worker, steering transfer or checkpoint exists. The sequence number is untouched and the old generation is fenced. `park_or_interrupt` re-queues on an unstarted pause, keeping waiters and media with the row. Compression remains not a pause point.

## Code
- `agent/update_pause_cancel.py`
- `gateway/run_turn_runner.py`
- `gateway/session_update_pause.py`
- `gateway/session_update_pause_hook.py`
- `gateway/session_update_pause_ingress.py`
- `hermes_state_update_pause.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_update_yield_before_turn.py`: `test_held_claim_yields_before_the_turn_starts_without_compressing`, `test_open_gate_runs_the_turn_normally`, `test_failing_gate_never_pauses`.
- `tests/gateway/test_update_hold_claimed_wake.py`: `test_wake_claimed_before_the_hold_goes_back_to_its_fifo_until_the_hold_lifts`, `test_human_turn_claimed_before_the_hold_still_runs`.
- `tests/hermes_state/test_requeue_unstarted_input.py`: `test_requeued_row_is_the_fifo_head_again_and_its_old_generation_is_fenced`, `test_refused_once_the_turn_wrote_its_transcript`, `test_refused_for_another_generation_or_owner`, `test_refused_after_settlement`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only turns that have not started can be re-queued; a turn that already wrote transcript rows falls back to the checkpoint path.
- Turn-start, idle and engine preflight compressions cannot yield and are intentionally not pause points.
