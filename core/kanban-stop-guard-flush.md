# Kanban stop guard flushes the worker's answer first

Patches: **1797** (`40e91500bf`, `fix(kanban): flush the worker's text answer before the stop-loop nudge's next request [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A worker's text-only final answer is flushed to a durable assistant row before the stop-loop nudge's next model request, so observed managed workers no longer fault closed.

## How it works

`agent/turn_stop_gates.py`, `turn_finalizer.py`, `session_persistence.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1797: The kanban stop guard appended a worker's text-only final answer as ephemeral scaffolding (_kanban_stop_synthetic) and continued straight into the next model request without flushing it. An observed managed worker (Ask Eve's private observer binds every SDK response to exactly one durable assistant row before the next SDK request) therefore faulted closed with "previous response never durably bound", and every retry hit "SDK request after observer close/fault".

## Code

`agent/conversation_compression.py`, `agent/session_persistence.py`, `agent/turn_finalizer.py`, `agent/turn_stop_gates.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_kanban_stop_interim_flush.py`: `test_budget_exhaustion_after_nudge_keeps_one_durable_answer`, `test_text_only_answer_is_durable_before_the_nudged_second_request`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
