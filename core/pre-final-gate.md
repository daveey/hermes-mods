# `pre_final_gate` hook: hold an answer silently, or append to it

Patches:

- `patches/0165-feat-agent-pre_final_gate-hook-silent-stop-gate-hold.patch`: the hook, the silent hold, the stream buffer and the config keys.
- `patches/0167-fix-agent-reset-pre_final_gate-attempts-outside-_PER.patch`: fix-up for 0165, moves one counter reset (see How it works).
- `patches/1701-daveey-pre_final_gate-tell-the-model-a-held-draft-wa.patch`: a buffered hold's nudge starts with a core "your draft was NOT shown" line (see "Held-draft notice").

What you see: a plugin can stop a final answer before it reaches the screen and make the agent try again. The user never sees the first answer, and it is not stored in the transcript. A plugin can also add a note to the end of an answer.

## Behavior

- New plugin hook `pre_final_gate(session_id, platform, model, final_response, turn_messages, attempt)`. It runs on every text stop, after verify-on-stop, `pre_verify` and the kanban worker guard.
- Return `{"hold": True, "nudge": str}`: the answer is not emitted as an interim message and not written to `state.db`. It stays only as the fallback if the iteration budget runs out. The model gets the nudge as a private synthetic user row and the turn continues.
- Return `{"append": str}`: the answer is delivered with `"\n\n" + text` added. The appended text is also streamed, so the screen matches the stored row.
- `agent.pre_final_gate_max` (default 2) caps holds per turn. At the limit a hold counts as deliver, and an `append` in the same dict still applies, which gives "deliver with the judge's note".
- A callback that raises, or runs longer than `agent.pre_final_gate_timeout` (default 30 s), is logged at WARNING and the answer is delivered unchanged.
- Callback attribute `needs_buffer = True`: assistant text deltas are buffered for the turn. They are released when a response turns out to carry tool calls, when the gate delivers, or at turn end. On a hold they are dropped. This is needed because no surface can retract streamed text: Desktop keeps any text that comes before a later tool call, and CLI and messaging streams cannot unprint. The cost is that answers appear in one chunk instead of streaming.
- Callback attribute `include_background = True`: the hook also runs in kanban workers, `delegate_task` subagents and cron. It is skipped there by default.
- Callback attribute `include_forks = True`: the hook also runs on detached fork turns (background review, `/btw` side questions: `_turn_origin` or `_persist_disabled` set). It is skipped there by default. `include_background` does not imply it; a fork that is also a background agent needs both. See "Fork turns" below.
- Held-draft notice (1701): when a hold's draft never streamed to the screen, core puts `HELD_NOTICE` before the hook's nudge, whatever the hook wrote. A draft that already streamed (unbuffered gate) gets the hook's nudge unchanged. See "Held-draft notice" below.

## Held-draft notice (1701)

A buffered hold hides the draft from the user, but the held assistant row stays in the model's context for the rest of the turn. Nothing told the model that, so a model could treat its held answer as delivered. In one recorded case a full explanation was held for a missing `session_outcome` call, and the model then sent only "The explanation and recommendations are complete."

- `agent.pre_final_gate.HELD_NOTICE` = `[runtime] Your draft above was NOT shown to the user. Your next reply replaces it: include its full content; don't refer to it as 'above'.`
- `held_nudge(nudge, shown)` returns `nudge` when `shown`, else `HELD_NOTICE + "\n\n" + nudge`. Both are exported in `__all__`.
- `apply_stop_gates` computes `shown` on the hold path **before** discarding the stream buffer: `shown = not decision.hold or agent._interim_content_was_streamed(final_response)`. With `needs_buffer` the draft was buffered, so `shown` is false and the notice is added. With an unbuffered gate whose draft already streamed, `shown` is true and the notice is left off, because it would be false.
- The same branch also handles a stop that is not a hold (`decision.hold` false, nudge falls back to `"Turn interrupted."`). There `shown` is true, so an interrupt-only stop gets no notice.
- Hooks cannot opt out, and need not write the notice themselves.

## Fork turns

A fork (`build_cache_parity_fork`: background review, `/btw`) shares the parent's `session_id` and `platform`, but it is not a reply in the conversation. A review fork's text is never shown. A `/btw` answer is shown, but as a tool-less side note outside the transcript (tools denied at dispatch, 3 iterations max), so a hold's nudge ("file a card", "publish the ask") cannot be acted on and only burns those iterations, and gates that key state by `session_id` (ask-gate, user-steps, session-outcome, chat-closeout) would mix fork text into the parent's state. `eligible_callbacks` therefore drops every callback without `include_forks` when `is_fork_turn(agent)` is true. The parent's own final answers stay gated. This matches `pre_verify` (skipped for forks since `966154e8d35`) and `post_llm_call`.

Why a core filter and not a `turn_origin` kwarg: `_call` passes kwargs through unfiltered, so a new kwarg breaks every callback without `**kwargs`. Coach's `envelope_gate` accepts `turn_origin` (hermes-mods a91fbc66), but core never passed it; the filter makes that check redundant but harmless. Excluding forks also stops a fork's stream from being buffered for a gate that won't run.

## How it works

- `agent/pre_final_gate.py`: finds eligible callbacks (via `iter_hook_callbacks`, with background contexts filtered out), runs each under a timeout, and returns a `GateDecision`. The first hold wins while under the limit, and appends add up.
- `agent/turn_stop_gates.py::apply_stop_gates`: on a hold, the candidate row and the nudge row are both flagged `_pre_final_gate_synthetic`. The buffer is discarded and the turn continues with the candidate as `pending_verification_response`. On deliver it flushes the buffer, and on append it rewrites the row and streams the suffix.
- `_pre_final_gate_synthetic` is listed in `session_persistence._EPHEMERAL_SCAFFOLDING_FLAGS` (never persisted), `turn_finalizer._VERIFICATION_CONTINUATION_FLAGS` (stripped from returned/live history) and `conversation_compression._SYNTHETIC_USER_FLAGS`.
- `agent/stream_delivery.py`: `_fire_stream_delta` and the scrubber-tail flush route through `_buffer_final_gate_text` while `_final_gate_buffer` is a list. `_emit_interim_assistant_message` and `turn_tool_round` flush first. `turn_context._reset_per_turn_agent_state` arms the buffer, and `finalize_turn` flushes and disarms it.
- `_pre_final_gate_attempts` is reset in `_reset_per_turn_agent_state` next to the buffer arm, not in the `_PER_TURN_RESET_STATE` tuple, which upstream edits (0167).
- `turn_final_response` now takes `final_response` from the stop-gate verdict, so an append reaches the stored row.

## Code

`agent/pre_final_gate.py`, `agent/turn_stop_gates.py`, `agent/stream_delivery.py`, `agent/turn_context.py`, `agent/turn_final_response.py`, `agent/turn_finalizer.py`, `agent/turn_tool_round.py`, `agent/session_persistence.py`, `agent/conversation_compression.py`, `hermes_cli/plugins.py` (`VALID_HOOKS`), `hermes_cli/config_defaults.py`, `website/docs/user-guide/features/hooks.md`.

## Tests

`tests/agent/test_pre_final_gate.py`, end to end through `AIAgent.run_conversation` with a scripted model that streams each answer:

- `test_hold_once_never_shows_or_persists_first_answer`: the held text never reaches the stream or interim callbacks. The second request carries the held answer and the nudge. Both hold rows are ephemeral, and the returned history is `[user, assistant(final)]`.
- `test_append_delivers_with_note`, `test_hold_limit_then_delivers_with_append` (attempts 0, 1, 2, then deliver with the note), `test_hook_exception_delivers_unchanged_and_logs`, `test_skipped_for_subagents_unless_opted_in`, `test_no_hook_streams_live`, `test_buffer_released_by_interim_and_discarded_on_hold`.
- Live: a throwaway plugin that holds once on a marker prompt, run against the Desktop backend over `/api/ws`. The first answer appeared in no `message.delta`/`message.interim`/`message.complete` event and not in the stored transcript, and the second answer did.
- `test_skipped_for_fork_turns_unless_opted_in` (parametrized: `_turn_origin` background_review / side_question, `_persist_disabled`): a control user turn is held, then gated; the same hook with only `include_background` is not called on the fork, which delivers its first answer unbuffered; with `include_forks` it runs again. RED before the filter (3 failed: the hook held the fork), GREEN after.
- `test_slow_hook_times_out_and_delivers`: a callback past `agent.pre_final_gate_timeout` is skipped and the answer is delivered.
- 1701: `test_hold_once_never_shows_or_persists_first_answer` now asserts the second request carries `f"{HELD_NOTICE}\n\nCheck first; do it yourself."`.
- 1701: `test_buffered_hold_nudge_starts_with_not_shown_notice` (parametrized over two hook nudges, a plain one and a `[session-outcome] ...` one): with a buffered hook, the second request carries `HELD_NOTICE` + blank line + the nudge, and the held draft never reaches the screen.
- 1701: `test_streamed_hold_nudge_has_no_not_shown_notice`: an unbuffered hook holds an answer that already streamed; the nudge reaches the model and `HELD_NOTICE` appears nowhere in the request.
- The mirror pass did not rerun the 1701 tests. Untested for 1701: the interrupt-only (`hold` false) branch's "no notice" outcome (no test drives it); a hold while `final_response` is not a string; Desktop/CLI rendering (the tests check the model's request and the stream callbacks, not a UI).

## Applying

Standalone on top of upstream's `pre_verify` stop gate (`agent/turn_stop_gates.py`).

1701 is not standalone: its hunks sit next to later additions in `agent/pre_final_gate.py` (the `replace` decision and `UnresolvedFallback` exports) and `agent/turn_stop_gates.py` (`unresolved_fallback`), and it calls `agent._interim_content_was_streamed`. Apply it in series order after those patches.

## Gotchas

- Without `needs_buffer`, a hold still hides nothing that has already streamed. Use it for any hook that holds.
- Already-running sessions keep the old code until their backend restarts.
- 1701: the notice is in the model's context only, never shown to the user. It is a synthetic `_pre_final_gate_synthetic` user row like any nudge, so it is not persisted.
- 1701: a hook that writes its own "not shown" line now produces it twice; drop it from the hook.
- 1701: the notice tells the model to resend the full content, but the model can still reply with a stub. Core does not compare the replacement with the held draft (see the session-outcome pairing below).

## Plugin?

No. Plugins have no surface that can stop a final answer before it is emitted and persisted, or keep streamed deltas back. The ask-gate plugin (`plugins/ask-gate`) builds on this hook.

1701 pairs with the public `session-outcome` plugin (`plugins/session-outcome`), which holds a reply missing its `session_outcome` call. After its own hold, if the next reply is under 40% (`RESTORE_RATIO = 0.4`) of a held draft of at least 800 characters (`RESTORE_MIN_CHARS`), the plugin returns `{"replace": <held draft>}` so the user gets the full answer. It logs the restore and appends it to `<home>/logs/held-draft-restores.jsonl`. It finds its held draft by the nudge row carrying its tag, with or without core's `HELD_NOTICE` prefix. Core's notice is the first defense (tell the model). The plugin's restore is a fallback for a stub reply after one specific gate's hold. Plugin tests: `plugins/session-outcome/tests/test_session_outcome.py` (`test_nudge_says_the_held_reply_was_not_shown`, `test_stub_after_own_hold_and_call_restores_the_held_draft`, `test_held_draft_found_without_core_prefix`, `test_short_held_draft_is_not_restored`, `test_another_gates_hold_is_not_restored`, among others).
