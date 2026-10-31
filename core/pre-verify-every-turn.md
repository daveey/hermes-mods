# pre_verify checks live final answers, never detached fork summaries

Patches: 0052 and 1364 (`966154e8d35`).

## Behavior

- **Default off** (`pre_verify_every_turn: False` in `DEFAULT_CONFIG["agent"]`):
  live turns consult the hook only after file edits.
- **On:** live text stops consult a registered `pre_verify` hook even without
  edits, supplying `changed_paths=[]` and the usual final response, attempt,
  coding flag, session ID, platform, and model.
- Hooks may return `{"action": "continue", "message": ...}` or
  `{"decision": "block", "reason": ...}` to request another turn.
- The existing `agent.max_verify_nudges` cap (default 3) still applies.
- Detached forks skip every `pre_verify` hook when `_turn_origin` is truthy or
  `_persist_disabled` is true, even with edits and every-turn checking enabled.
  This covers background skill/memory review and side-question summaries that
  share a parent session ID but are not the user's reply.
- Live turns still call the hook with their parent session identity.

## How it works

`pre_verify_every_turn(config=None)` reads the opt-in using `is_truthy_value`;
truthy strings such as `"yes"` are accepted (that spelling is untested here).
`_pre_verify_nudge` first checks the fork markers and returns `None` immediately.
It emits one INFO line per skipped invocation:
`pre_verify skipped for fork turn origin=<origin> session=<sid>`.
An absent origin is logged as `persist_disabled`; logging is untested by the new cases.
This aligns with `post_llm_call`, which the finalizer already skips for
`_persist_disabled`. No finalizer change is included in 1364.
For a live turn, the remaining condition is a registered hook, attempts below
the cap, and either edits or the every-turn opt-in.

## Code

- `agent/verify_hooks.py`: option reader and export.
- `agent/turn_stop_gates.py`: `_pre_verify_nudge` and detached-fork guard.
- `hermes_cli/config_defaults.py`: default off.

## Tests

In `tests/agent/test_verify_hooks.py`:
- `TestPreVerifyEveryTurn`: `test_off_by_default`, `test_opt_in`.
- `TestPreVerifyGateWithoutEdits`: `test_no_edits_default_skips_hook`,
  `test_no_edits_opted_in_calls_hook_with_empty_paths`, `test_edits_still_call_hook_when_off`.
- `TestPreVerifySkipsForkTurns`:
  `test_background_review_fork_never_calls_hook` (both marks),
  `test_turn_origin_alone_skips_hook`, `test_persist_disabled_alone_skips_hook`,
  `test_live_turn_still_calls_hook` (control).

Tests were inspected, not executed for this documentation task. The new tests
invoke the stop gate directly, not the full background-review/side-question launchers.

## Applying

0052 builds on upstream's `pre_verify` hook and `max_verify_nudges` support.
Apply 1364 later in mirror series order. No new option is needed for fork exclusion.

## Gotchas

Every eligible live text stop pays for its hook call, and a continue result
costs another model turn. Hooks should scope themselves by paths/coding/attempt
and tolerate empty `changed_paths`. The cap and return-shape contracts are
existing behavior, not separately exercised by the four fork regressions.

## Plugin side

Core must decide whether hooks are called; a plugin alone cannot make this guard.
The companion coach hook also returns `None` for a `turn_origin` kwarg, and its
`signals.py` routes a failed revision absent from `state.db` as
`coach/judged_unsent_draft`. Those plugin changes are supplied context, untested
by this core patch's tests.
