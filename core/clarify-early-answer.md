# A decision recorded elsewhere ends a blocked `clarify`

Patch:

- `patches/0814-feat-clarify-a-decision-recorded-in-the-For-you-queu.patch`: `tools/clarify_early.py`,
  `tools/clarify_tool.py`, `agent/inline_tool_executors.py`, `tui_gateway/agent_callbacks.py`,
  `tui_gateway/server_requests.py`.

What you see: a chat asks you a question with `clarify`, but you already answered the same thing in the user-steps
"For you" queue. Before, the chat sat on the question until the clarify timed out (an hour), because the queue's
decision can only be delivered as a new turn and the turn was busy waiting on you. Now the clarify returns your
queued decision within a couple of seconds.

## Behavior

- A plugin registers a provider with `tools.clarify_early.register_provider(fn)`;
  `fn(session_id, tool_call_id) -> EarlyAnswer(result, commit) | None`. The `user-steps` plugin is the only provider
  (it answers only for decisions owned by that session or a compression ancestor/descendant of it).
- Before showing a question, `clarify_tool` asks the providers; an answer is returned instead of asking.
- While the Desktop (tui_gateway) bridge is blocked on the human, the providers are polled every
  `POLL_INTERVAL_S` (2 s). On an answer the bridge's open `clarify` server request is withdrawn
  (`callback.cancel_early`, `server_requests.cancel(sid, reason, method="clarify")`; other open requests of that
  session are left alone) and the tool returns
  `{"status": "answered_elsewhere", "outcome": "answered_elsewhere", "decisions": [...], "notice": "David answered in the For-you queue: ..."}`.
  It has no `responses` key: it is never shaped like text the user typed.
- `commit()` (provider marks its decisions delivered) runs only when that early answer is the one returned. If the
  human answered first, their answer stands and the decision stays undelivered.
- A surface whose callback has no `cancel_early` (plain CLI, messaging gateway) gets only the check before asking.
  The messaging-gateway path is untested.

## How it works

- `clarify_early.ask` runs the callback on a worker thread (context copied) only when a provider exists, the call
  has a session id and the callback has `cancel_early`; otherwise it calls it inline as before.
- The inline executor passes `agent.session_id` and the call's `tool_call_id` to `clarify_tool`.
- Provider exceptions are swallowed (debug log): a broken store never blocks a question.

## Tests

- `tests/tools/test_clarify_early_answer.py`: check before asking (callback never runs); decision recorded while
  blocked ends the clarify and commits; a provider answering only for another session leaves the clarify waiting;
  a real answer that wins the race is not replaced and nothing commits; provider failure is harmless; no
  session/no cancel hook means no polling; the inline executor passes session and tool-call ids.
- `tests/tui_gateway/test_protocol.py::test_clarify_callback_cancel_early_ends_only_the_clarify_request`.
- Plugin side (hermes-mods `plugins/user-steps/tests/test_clarify_early.py`): store, receipt, lineage, guard and an
  end-to-end run against this core.

## Applying

Core plus the `user-steps` plugin from the same hermes-mods commit; no config. Older cores simply lack the module and
the plugin registers nothing. Takes effect in a backend after it restarts on the new core.

## Gotchas

- Busy refusals no longer count toward the plugin's retry cap (plugin change, not core).
- Cancelling discards any partially locked batch answers of that clarify.
