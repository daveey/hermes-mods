# Continue a managed Kanban run after its owner restarts

Patches **2200–2204**.

## Behavior

- A managed turn cleanly interrupted by gateway restart/shutdown can continue with the same run, claim, submitter process and session, rather than consuming a new attempt.
- The worker waits through the owner replacement gap, including an update that disconnects before a terminal receipt arrives. Checkpointed continuation has a 300-second silence budget; an ordinary owner gap keeps the 180-second budget. A worker accepts at most eight distinct continuation checkpoints.
- Interrupted tool calls carry `[interrupted by Hermes update at <UTC>: re-run if still needed]` into the continued conversation. Finished results and results before a later user turn are preserved.
- A lost managed child, a stop before child bootstrap, or an unsafe continuation uses the uncounted restart-requeue fallback. The pre-bootstrap case logs why no checkpoint was created.

## How it works

The stopping owner books a `restart_checkpoint` event (`reason: core_update_pause`) and a continuable `worker_result` with a one-time token before settling the interrupted admission. The submitter retains its claim and reconnects. It also checks the board for an unconsumed checkpoint when a refusal gap reaches the ordinary owner timeout.

The replacement owner requires the same session and interrupted admission, a readable non-empty transcript not rotated by compression, and a still-current task/run/claim/checkpoint. Preparation is serialized per session. It repairs missing trailing tool results and rewrites the stopped tool block's cancelled/skipped/interrupted results in place, scanning at most 256 recent rows. The idempotent continuation request is `<original request>#continue:<token>`; `owner_admitted` and `restart_continued` record it. A draining owner may prepare the marker before submit refuses; the successor reuses that request identity.

The continued turn adds only the continuation note because skills and card context are already in history. Once admitted, the worker clears its continuation-wait state: final owner errors are final again. Unsafe preparation raises `kanban_continuation_unsafe` and records `restart_continue_refused`; fallback exit 76 is reclaimed without increasing consecutive failures. Pre-bootstrap interruptions are explicitly excluded even when an earlier run's copied history is non-empty.

## Code

- `gateway/session_kanban.py`: continuation admission and prompt selection.
- `gateway/session_kanban_continue.py`: safety checks, serialized preparation, transcript repair and continuation events.
- `gateway/session_kanban_restart.py`: checkpoint versus plain restart-loss booking.
- `gateway/session_managed_worker.py`: bootstrap flag, checkpoint selection and fallback logging.
- `hermes_cli/kanban_worker_client.py`: checkpoint consumption, bounded reconnect and fallback.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**:

- `tests/gateway/test_kanban_restart_continuation.py::test_a_real_owner_restart_mid_turn_continues_the_same_run`: two real gateway owners and the dispatcher worker, parameterized for interruption during a model call and during a `terminal` call; asserts same run/claim/worker/session, completed continuation, no reclaim/crash, and exact update text on the terminal path. The probe is `tests/gateway/fixtures/kanban_restart_continue_probe.py`.
- The same suite covers worker reconnect, extended update-gap waits with/without a checkpoint, final errors after readmission, one-time tokens, restart-storm limit, lost-child booking, unsafe session/admission/claim cases, one result under racing prepares, cancelled/skipped/killed versus finished results, and the later-user-turn boundary.
- `tests/gateway/test_core_update_kanban_wait.py`: cooperative Stop booking and `test_a_stop_before_the_child_bootstrapped_is_not_a_checkpoint`, including its INFO log assertion.
- No separate `execute_code` interruption integration case is supplied; that entry path is untested here.

## Applying

Use only the complete ordered replay recipe in [README.md](README.md), including its bridges and verification steps. These patches are not an independent cherry-pick/apply set. Restart onto the resulting core for new owners and workers to use the protocol.

## Gotchas

An interrupted call may already have produced a side effect. The continuation note requires checking current state before repeating it; the marker is not proof that nothing happened. Continuation is not recovery from an arbitrary crash or an unlimited restart wait. A pre-bootstrap turn must receive its original first prompt on a later attempt, not a continuation note against old history.

## plugin?

No. Checkpoint booking, runtime admission, transcript mutation and worker claims require core coordination; a plugin cannot provide the same ownership guarantees.
