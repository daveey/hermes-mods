# Process exit mid-turn logs one line, not a traceback

Patch: 0620 (daveey/hermes `c5153adda1`).

## Behavior

A turn finishing while the backend exits hit `pre_final_gate._invoke` after the main thread returned; the pool submit raised "cannot schedule new futures after interpreter shutdown" (or "can't register atexit after shutdown"), logged as a WARNING with a full traceback in errors.log and filed by self-heal as a defect (agent-9a2a3cc05d). Now shutdown errors log one INFO line and fail open exactly as before, and the errors.log handler collapses any record whose exception is a process-exit scheduling error to one line (agent.log keeps the traceback), so future thread-starting sites need no opt-in.

## How it works

`tools.interpreter_shutdown` also recognises the atexit-refusal message; `pre_final_gate._invoke` and the `tool_executor` sequential error path use it; `hermes_logging._ProcessExitNoiseFilter` is attached to the errors.log handler.

## Code

- `agent/pre_final_gate.py`, `agent/tool_executor.py`, `hermes_logging.py`, `tools/interpreter_shutdown.py`.

## Tests

- `tests/agent/test_pre_final_gate_interpreter_shutdown.py` (subprocess gate after main exit, both CPython messages; tool path; real-error controls), `tests/test_hermes_logging.py` (filter).

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- The filter matches CPython's message text; a reworded message would restore the traceback.

## Plugin?

No. It changes core code that runs before or outside plugin loading.
