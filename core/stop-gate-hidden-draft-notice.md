# Verification gates that hide a buffered draft say so; pre_verify restores a stubbed draft

Patch **2002**: source `9acf86e92864e085017f7c9bcde40bc039cc4c58`, "[daveey] stop gates: verification holds that hide
a buffered draft say so; pre_verify restores a stubbed draft". This extends the held-draft notice in
[pre-final-gate.md](pre-final-gate.md) (patch 1701), which covered only the `pre_final_gate` hold.

## Behavior

When a `pre_final_gate` hook that needs a stream buffer is active, its buffer can hide a draft. The legacy
verification gates then keep that draft out of both the screen and the session DB; the model still sees it.
Until now only a `pre_final_gate` hold prefixed its nudge with `HELD_NOTICE`, so for the other gates the
model believed the hidden draft had been delivered and replied with a short delta. The user never saw the
answer.

- **Notice on every hiding gate.** The nudge from each legacy verification gate now starts with
  `HELD_NOTICE` + a blank line whenever the draft was hidden. `HELD_NOTICE` is "[runtime] Your draft above
  was NOT shown to the user. Your next reply replaces it: …". The affected gates are late-LSP verdicts,
  verify-on-stop, `pre_verify` and the Kanban stop nudge. When the draft actually streamed (no buffer), the
  nudge is unchanged.
- **pre_verify safety net.** `pre_verify` asks for more work, never a retraction. Suppose the hidden draft
  of a `pre_verify` continue is at least 800 characters, and the final reply is under 40 % of its length
  and doesn't contain it. Then the draft is delivered ahead of the reply, separated by a blank line, and
  `restored_hidden_draft hook=pre_verify draft_chars=… reply_chars=… session=…` is logged at INFO.
  - A full-length revision isn't touched, and neither is a draft under 800 characters.
  - A `pre_final_gate` *replacement* in the same stop clears the remembered draft. A replacement
    retracts, so the hidden text never comes back.
  - Verify-on-stop, late-LSP and Kanban-stop drafts get the notice but no restore.

## How it works

- `agent/turn_stop_gates._append_interim_answer` now returns whether the answer reached the screen. It
  returns `False` when a final-gate buffer hid it.
- Each caller in `_apply_verification_gates` wraps its nudge in `agent.pre_final_gate.held_nudge(nudge, shown)`.
  That function already existed (1701).
- On a hidden `pre_verify` continue, `_remember_hidden_draft` keeps the longest stripped draft in
  `agent._pre_verify_hidden_draft`. This per-turn field is reset in `agent/turn_context._reset_per_turn_agent_state`.
- `apply_stop_gates` handles the remembered draft.
  - When a `pre_final_gate` decision has no replacement, it calls
    `_restore_hidden_draft(agent, final_msg, final_response, deliver=not stream_deferred)`. That rewrites
    `final_msg`'s `content`, or its `api_content` when only that is set. It streams the prefix when the
    stream isn't deferred.
  - A replacement clears the remembered draft.
  - The thresholds are `RESTORE_MIN_CHARS = 800` and `RESTORE_MAX_RATIO = 0.4`.

## Code

`agent/turn_stop_gates.py`, `agent/turn_context.py`.

## Tests

I didn't run these tests for this document. They're listed from the source diff, in `tests/agent/test_pre_final_gate.py`.

- `test_buffered_pre_verify_nudge_starts_with_not_shown_notice`: the second request carries
  `HELD_NOTICE\n\n[coach] Fix it.`, and no interim is shown.
- `test_streamed_pre_verify_nudge_has_no_not_shown_notice`: the control case with no buffer.
- `test_stub_revision_after_hidden_pre_verify_draft_restores_draft`: the final response is draft + stub. Both
  are shown, the last message content matches, and the log line is present.
- `test_full_length_revision_after_hidden_pre_verify_draft_is_untouched`.
- `test_short_hidden_pre_verify_draft_is_not_restored`.
- `test_buffered_verify_on_stop_nudge_also_says_not_shown`: the notice is present and nothing is restored.

**Untested:**

- The late-LSP and Kanban-stop gates. They go through the same `held_nudge(nudge, shown)` call, but no
  test drives them under a buffer.
- The clear-on-`pre_final_gate`-replacement branch.
- The `api_content`-only rewrite.
- Gateway and Desktop rendering of a restored draft. The tests drive the agent loop with a fake transport.

## Applying

Apply with the series (README § Applying), after 1701 (`held_nudge`/`HELD_NOTICE` in `agent/pre_final_gate.py`).

## Gotchas

- **Thresholds are heuristics.** A deliberately short final answer (under 40 % of an 800+ character hidden
  draft) gets the draft prepended. That's by design, because `pre_verify` never asks for a retraction. But a
  hook that does ask to drop the draft must use a `pre_final_gate` replacement, not `pre_verify`.
- **Longest draft only.** Only the longest hidden `pre_verify` draft in the turn is remembered.

## Plugin boundary

The `pre_verify` and `pre_final_gate` hooks are plugin hooks, but where the nudge text is placed and the
hidden-draft bookkeeping happen inside core's stop-gate loop. A plugin can't tell whether its draft reached
the screen, so this stays in core.
