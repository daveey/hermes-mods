# pre_verify can check every final answer, not only ones after edits

Patches:

- `patches/0052-feat-agent-agent.pre_verify_every_turn-fires-pre_ver.patch`: agent. New config key
  `agent.pre_verify_every_turn` fires the `pre_verify` plugin hook on text stops with no file edits.

What you see: nothing, unless a plugin registers `pre_verify` and you set `agent.pre_verify_every_turn: true`. Then
that plugin gets a say on every final answer, for example to catch a hand-off or an unchecked "done", and can keep
the agent going with a message. Stock Hermes only asked the hook after a turn that edited files.

## Behavior

- **Default off** (`pre_verify_every_turn: False` in `DEFAULT_CONFIG["agent"]`). Behavior is then upstream: the hook
  runs only when the turn changed files.
- **On:** when the agent stops with a text answer, a registered `pre_verify` hook is called even with no edits. It
  gets `changed_paths=[]`, plus the usual `final_response`, `attempt`, `coding`, `session_id`, `platform`, `model`.
- The hook keeps the turn going the same way as before: return `{"action": "continue", "message": ...}` (or
  `{"decision": "block", "reason": ...}`).
- The existing cap still applies: at most `agent.max_verify_nudges` (default 3) continue nudges per turn, so a hook
  can't trap the loop.
- The value is parsed with `is_truthy_value`, so `"yes"` counts as true.

## How it works

- `agent/verify_hooks.py`: new `pre_verify_every_turn(config=None)` reads `agent.pre_verify_every_turn`, exported in
  `__all__`.
- `agent/turn_stop_gates.py`, `_pre_verify_nudge`: the condition changed from
  `_edited and has_hook("pre_verify") and attempt < max_verify_nudges()` to
  `has_hook("pre_verify") and attempt < max_verify_nudges() and (_edited or pre_verify_every_turn())`.
  `get_pre_verify_continue_message` is called unchanged.
- `hermes_cli/config_defaults.py`: adds the default with a comment.

## Tests

`tests/agent/test_verify_hooks.py`:

- `TestPreVerifyEveryTurn`: `test_off_by_default`, `test_opt_in`.
- `TestPreVerifyGateWithoutEdits`: `test_no_edits_default_skips_hook`,
  `test_no_edits_opted_in_calls_hook_with_empty_paths`, `test_edits_still_call_hook_when_off`.

## Applying

Standalone. It builds on the upstream `pre_verify` hook and `max_verify_nudges` in `agent/verify_hooks.py`.

## Gotchas

- With this on, every hooked text stop pays for the hook call, and a hook that says "continue" costs a model turn.
  Hooks should scope themselves (check `changed_paths`, `coding`, `attempt`, `final_response`).
- Hooks written for the old contract may assume `changed_paths` is non-empty. Check yours before turning this on.

## Plugin?

No. The plugin can supply the `pre_verify` hook, but whether the hook is called at all is decided in core
(`_pre_verify_nudge`), so the gate needs this patch.
