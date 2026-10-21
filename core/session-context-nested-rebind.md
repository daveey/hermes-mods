# Nested session re-bind restores the turn binding on clear

Patches:

- `patches/0159-fix-session-context-a-nested-re-bind-of-the-same-ses.patch`: `clear_session_vars` restores the prior values when the tokens re-bound the session key already bound.

What you see: Kanban cards a Desktop chat creates after a queued model switch keep their session id and tui subscription, so the sidebar counts them and the chat hears when they land.

## Behavior

- Cause: a queued model switch refreshes the stored system prompt on the Desktop turn thread (`_persist_live_session_system_prompt`), which re-binds the turn's own session and clears it in a `finally`. `clear_session_vars` wiped every var to `""`, so the rest of the turn ran with no `HERMES_SESSION_KEY`/`ID`; `kanban_create` then stored `session_id` empty.
- Now: when the tokens re-bound the session key that was already bound (same non-empty key), clear restores the outer values, including the session cwd. Any other clear still wipes, so a task that inherited another session's vars never falls back to that identity.

## How it works

- `gateway/session_context.py`: `set_session_vars` now keeps the cwd token; `_nested_rebind_tokens(tokens)` returns `{var: token}` only for the same-key re-bind; `clear_session_vars` restores `Token.old_value` for those (mapping `MISSING` to the module's unset value) and otherwise wipes as before.

## Code

`gateway/session_context.py`.

## Tests

- `tests/gateway/test_session_env.py`: `test_nested_rebind_of_same_session_restores_outer_binding`; binding another session then clearing, and clearing without tokens, still wipe.
- `tests/gateway/test_turn_session_context_nesting.py`: the turn keeps its binding across the system-prompt refresh, and `kanban_create` still resolves its target afterwards.

## Applying

Standalone.

## Gotchas

- Restoring applies only to same-key nesting; do not widen it, because wiping on a different key is what stops async tasks inheriting a stale identity.

## Plugin?

No. This is the gateway's context-variable plumbing.
