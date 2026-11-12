# A refusal caused by earlier history compacts once and retries

Patches: **2539, 2540**.

## Behavior

A safety-filter refusal on a turn's first model call, before any tool round, can be caused by the chat's earlier history rather than the new message. The old recovery dropped the refused turn and resent the same history, and told the user to just ask something else, so every later message was refused too. Now Hermes compacts the earlier history once and resends the same message. If the summary cannot commit, or the retried request is refused, the turn ends with copy that names the history as the cause and points at `/new` (in a Bot Chat, Archive it). Refusals after a tool round, on a chat's first message, or when compaction cannot run keep the drop-and-notice path. A status line says the filter refused earlier history and compaction is being retried.

## How it works

- `handle_content_policy_refusal` (`agent/turn_truncation.py`), after the configured-fallback attempt, calls `history_refusal_step` in the new `agent/turn_refusal_history.py`.
- `is_history_triggered`: the refused request held earlier history plus only this turn's user row. It is checked first, so a refusal after a retry that ran a tool round keeps the tool-round copy (2540).
- Compaction: `agent._compress_context(head, system_message, force=True, trigger="content_policy_history", verbatim_tail=[current])` with the compressor's head/tail protection minimized for the call. The caller's `system_message` is passed through unchanged and the returned prompt is adopted; passing the full built prompt doubled it and stored the copy in the session DB (2540).
- Outcomes: `retry` (arms `restart_with_compressed_messages`; one per turn via `_history_refusal_retry_turn`), `refused`, `summary_refused`; `compaction_skipped` or a compaction error returns None. Each logs `history-triggered refusal: compacted+retried outcome=<...>`.
- `RefusalVerdict` gains `messages`/`conversation_history`; `check_api_response` in `agent/turn_response_check.py` accepts `system_message` and swaps in the compacted request. Related: [provider-refusal-turn-boundary.md](provider-refusal-turn-boundary.md).

## Code

`agent/turn_refusal_history.py` (new), `agent/turn_truncation.py`, `agent/turn_response_check.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/agent/test_content_policy_history_trigger.py` (step outcomes: retry, summary refused, retry refused without a second compaction, tool-round and first-message paths, system-message pass-through), `test_content_policy_history_trigger_loop.py` (answered after one compaction; a second refusal ends with the new-chat copy after two calls, through `run_conversation`), and `test_content_policy_history_trigger_db.py` (real `SessionDB` and compressor: system prompt kept, one current user row). Covered entry path: the agent conversation loop only; gateway, Desktop and TUI surfaces are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A trigger the compressor must keep (latest user turn and reply; the lean summary quotes earlier user messages) can survive, in which case the retry is refused and the turn closes with the history copy.
- One compaction and one retry per turn.

## Plugin

Core change; no plugin required.
