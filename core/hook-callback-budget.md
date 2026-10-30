# Per-callback hook budgets and a host deadline/abandon signal

Patches:

- `patches/1059-fix-plugins-per-callback-hook-budgets-and-a-host-dea.patch`

A turn-end `pre_verify` checker that makes bounded model calls (the coach: judge 60 s + confirm 90 s) ran under the global 30 s `plugins.hook_callback_timeout`. The host abandoned it, suppressed the callback for 60 s (so the next turn's check was skipped), and the abandoned worker still recorded a nudge the host had already discarded.

## Behavior

- A plugin hook callback may declare its own bound: `callback.hermes_hook_timeout = N` seconds. The value is clamped to 600, applies to bounded hooks only, and never turns the threaded path on when `plugins.hook_callback_timeout` is 0 or lower. An invalid value is ignored.
- On the worker thread, `hermes_cli.plugins.current_hook_call()` returns the host's `HookCallBudget`: `remaining()` seconds and an `abandoned` `threading.Event` that is set the moment the host stops waiting. A callback that finishes late is logged as `result discarded`.
- Hook call identity falls back to `session_id`, so one session's running `pre_verify` no longer makes the host skip another session's.

## How it works

`plugins_dispatch._run_hook_callback_bounded` resolves the timeout with `hook_callback_timeout_for(callback)`, creates a `HookCallBudget` (deadline, abandoned event) that the worker reads through `current_hook_call()`, and sets `abandoned` on timeout. `_hook_call_identity` keys the skip/suppress bookkeeping on the callback plus session id.

## Code

- `hermes_cli/plugins_dispatch.py`: `HookCallBudget`, `current_hook_call`, `hook_callback_timeout_for`, `_hook_call_identity`, `_run_hook_callback_bounded`.
- `hermes_cli/plugins.py`: re-export of `current_hook_call`.
- `website/docs/user-guide/features/hooks.md`: documents the attribute.

## Tests

`tests/hermes_cli/test_hook_callback_budget.py` (15 tests): a declared budget outlives the global one and still abandons past it; invalid and clamped attributes; no threading when the global timeout is disabled; the deadline is visible on the worker; the abandoned flag and the `discarded` log; end-to-end `get_pre_verify_continue_message`; a second session's `pre_verify` is not skipped; the same session is deduplicated.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

hermes-mods `plugins/coach` declares 85 s and fits its model calls into the remaining budget. On older cores it falls back to the global timeout.
