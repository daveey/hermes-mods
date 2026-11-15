# Session-scoped final buffering after pre-call hooks

Mirror batch 6; ordinals 4059.

## Behavior

Dynamic final gates arm buffering after all pre_llm_call hooks and before inference. Session-specific eligibility prevents another armed session from suppressing ordinary streaming, while held drafts stay out of the stream sink.

## How it works

eligible_callbacks retains existing background/fork opt-ins. needs_buffer prefers a synchronous needs_buffer_for_session(session_id=...) predicate; false overrides even a true global flag. Predicate errors log and fall back only to a non-callable legacy flag. The once-per-turn reset clears stale buffer/attempt state, then build_turn_context initializes the buffer after hooks. Continuations/retries do not repeat that prologue, preserving held text handling and the per-turn hold budget.

## Code

Source paths touched by this feature group (including regression fixtures):

- `agent/pre_final_gate.py`
- `agent/turn_context.py`
- `tests/agent/test_session_final_buffer.py`

Mail entries in the ordered series:

- 4059: [`3c0d856312756637a976519ed8315cffa4356c33`](patches/4059-daveey-Arm-final-buffering-after-hooks-with-session-scoped-eligibility.patch)

## Tests

- `tests/agent/test_session_final_buffer.py`: real turn setup, plugin-hook dispatch, stream sink, tool executor/result-row construction and continuation are exercised with scripted model replies and an external read boundary. The initial rejected draft is absent from immediate and final shown output; a real tool result precedes the corrected final answer.
- The same file checks ordinary-session streaming while a different session is armed, next-turn stale-buffer/attempt reset, once-per-turn hold across continuation, cancellation discard, predicate-over-legacy true/false, logged error fallback, legacy callable rejection, scope exclusion before predicate and explicit background/fork opt-ins, and last-pre-hook control of initialization.
- Source/parent evidence reports **84 focused tests and 889 consumer tests**, with **four mutants killed**. These are reported results, not executions performed here. The new file’s integration boundary is deterministic; it is not live Desktop/CLI display verification.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

This final patch is required; the earlier global-flag sampling point misses dynamically armed gates. Buffers cannot retract text already shown, so initialization must precede inference. A hold is bounded, not an indefinite veto; off/ineligible scopes retain ordinary behavior. The focused suite includes hold-cap, legacy and off/scope controls, but does not establish installed-plugin activation or live display suppression.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

This is the core contract used by dynamic pre_final_gate plugins. Plugins may expose a session predicate and opt into background/fork scopes, but cannot repair core’s pre-hook sampling order on their own.
