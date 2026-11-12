# A refusal caused by earlier history compacts once and retries

Patches: **2539, 2540, 2590**.

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

## Follow-up 2590: rotation-mode retry sends the current message once

- **Behavior:** under the legacy `compression.in_place: false` (rotation mode), the history-refusal retry no longer repeats the current message. Before, the child session stored it folded onto itself (`U\n\nU`) and the retry request carried it a third time. Now the child session holds it once and the retry request ends with it exactly once. A refusal handled where no turn identity is available keeps the drop-and-notice path instead of compacting and retrying.
- **How it works:**
  - `adopt_grown_durable_parent` (moved from `agent/conversation_compression.py` to the new `agent/conversation_compression_adopt.py`, imported back under its old name `_adopt_grown_durable_parent`) takes the caller's `verbatim_tail`. When the adopted durable parent already ends with the tail's persisted rows (same role and content), it drops them from the adopted end, so the later rejoin of the tail does not fold it onto itself. The same adoption path serves a rotation-mode `/compress here N`; no test drives that command (untested).
  - `history_refusal_step` (`agent/turn_refusal_history.py`) ends the retry request via the new `_with_current_once`: it pops trailing user rows whose content only repeats the current text (including a folded copy), then appends the current message unless the last row already is it.
  - The one-retry cap is now keyed on `turn_id`, falling back to the agent's `_current_turn_id`. With neither, `history_refusal_step` returns None (drop-and-notice), because the cap could not tell a refused retry from a new turn and would compact and retry forever.
- **Code:** `agent/conversation_compression.py`, `agent/conversation_compression_adopt.py` (new), `agent/turn_refusal_history.py`.
- **Tests:** source coverage, **not run in this mirror**: `tests/agent/test_content_policy_history_trigger.py` (folded-head and no-turn-id unit cases) and `test_content_policy_history_trigger_db.py` (rotation-mode variant with a real `SessionDB`: the request's last user content equals the current message once, and the child session rows hold it once). Entry path: the agent conversation loop only, as for the earlier entries.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A trigger the compressor must keep (latest user turn and reply; the lean summary quotes earlier user messages) can survive, in which case the retry is refused and the turn closes with the history copy.
- One compaction and one retry per turn.

## Plugin

Core change; no plugin required.
