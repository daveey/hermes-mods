# Terminal completion delivery

## Behavior

A terminal job's persisted final result is an outbox, independent of the tool
caller's lifetime. A host-local reconciliation loop revisits old watchdog jobs
and jobs that finished before their worker recorded its block.

- A still-blocked card explicitly naming its job is resumed by core on `ran`.
  The transition and result comment share one transaction. `declined` comments
  once without unblocking. Newer attachments and unrelated blocks are untouched.
- Gateway-owned local chats receive final results through the existing trusted
  automation FIFO. The notice contains job ID, state, exit code, and at most
  6,000 characters of cleaned output tail. It is not viewer-authored input.
- The producer freezes its payload before admission. Core's unique admission
  identity, `["user-terminal", job_id, "final"]`, survives missing acknowledgments
  and repeated sweeps. A local receipt records accepted admission, **not** model
  completion. Missing sessions receive a terminal `missing` receipt.
- The plugin lifecycle owns the host-local sweep; unload stops future passes.
  Detached watchdogs and managed workers do not execute session turns.

The worker ask is one step: “Authenticate in the window; the card resumes by
itself when the command finishes. Unblock = resume without the result; archive
= abandon.” A resumed worker still reattaches with the same `job_id`.

## Scope and verification status

Tests exercise the gateway's local CLI and GUI admission paths, including cold
owner restoration, compression-tip ownership, all five final states, missing
sessions, immutable payload retry, and wrong-profile isolation. Model execution
is suppressed in these isolated tests; they do not prove live chat delivery.
Legacy inline TUI without a gateway authority is not covered by this delivery
adapter. It retains the pending result rather than submitting fabricated viewer
input. Native messaging sessions and remote-courier completion are not yet
verified. Real iTerm chat/worker acceptance and deployed-backend verification
remain required before this change is considered complete.

## Tests

Use the installed core test interpreter with `PYTHONPATH` set to the core
checkout and `HERMES_DISABLE_LAZY_INSTALLS=1`:

```
python -m pytest -q -p no:cacheprovider --confcutdir=plugins/user-terminal \
  plugins/user-terminal/tests/test_completed_jobs.py \
  plugins/user-terminal/tests/test_session_completion.py \
  plugins/user-terminal/tests/test_completion_lifecycle.py \
  plugins/user-terminal/tests/test_closed_asks.py
```

`test_completed_jobs.py` covers worker watchdog and sweep entry paths, transactional
rollback/retry, late block, repeated-pass dedup, and unrelated-card exclusion.
`test_session_completion.py` uses real core authority and SQLite admission, not a
mocked notification helper. `test_completion_lifecycle.py` covers host publication,
plugin unload, managed-worker exclusion, and foreign profile isolation.

## Why the result was lost

1. A command could finish after its originating tool turn ended. The old
   `wait_for` returned only to its active caller; final `watch_once` had no
   completed-result dispatch.
2. Terminal status persistence and owner continuation were separate lifecycles,
   with no durable final-result consumption identity.
3. Card guidance required manual unblock, transferring a result the job store
   already knew into a second human action.
4. Existing tests covered synchronous PTY results and closed-window cleanup,
   not ended-turn completion admission or final-before-block races.

The countermeasures are durable owner admission, transactional card transition,
periodic old-job reconciliation, and explicit entry-path regression tests. Against
baseline `176d175d`, the new session/card tests report 29 failures and 8 passing
safety cases; the fixture-only core import shim supplies no completion behavior.
