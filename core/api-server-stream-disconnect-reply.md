# API server: a stream disconnect during worker teardown keeps the reply

Patches: **1912** (`4dbe5d6966`, `fix(api_server): session-stream disconnect during worker teardown no longer discards the reply`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A client disconnect from `POST /api/sessions/{id}/chat/stream` that lands after the worker unregistered its agent but before the loop saw the result no longer cancels a finished turn and discards its reply.

## How it works

The route passes an `agent_ref` to `_run_agent` (set once the agent is built, never cleared); the drain cancels only when no agent was ever built, otherwise it interrupts and awaits the turn. The drain moved to `gateway/platforms/api_server_session_stream.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1912: The disconnect drain for POST /api/sessions/{id}/chat/stream decided "never started -> cancel" from `_active_run_agents` alone. `_run_agent`'s worker unregisters the agent in its `finally`, before the loop sees the result, so a client disconnect landing in that window cancelled a turn that had already finished: the run read "cancelled" with no output and the handler raised CancelledError (api_server.py:3790). That is the CI flake of test_session_stream_records_reply_text_for_post_disconnect_recovery (run 41592552337): its test thread releases the agent right after run_started, so on a throt...

## Code

`gateway/platforms/api_server.py`, `gateway/platforms/api_server_session_stream.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_session_api.py`: `test_session_stream_disconnect_during_worker_teardown_keeps_the_reply`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
