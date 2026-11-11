# Profile- and owner-scoped rename suggestions

Patches **2148, 2151**.

## Behavior

`session.title.suggest` generates a read-only rename suggestion using the selected profile's credentials on the legacy gateway and the session owner's transcript and secret scope on the authority gateway. It does not save a title. Provider failure may still return no suggestion, but is logged at warning level rather than debug level.

## How it works

The legacy handler gains `_profile_scoped`, matching other auxiliary-model operations. `suggest_short_name` retains its fail-open `None` result while logging exception type and message.

The authority handler is registered alongside `llm.oneshot`. It accepts a nonempty string `session_key` or `session_id`, constructs the profile-scoped `SessionRef`, and requires `session:read` authorization. A `DeferredReply` reads messages from `authority.db` and invokes `suggest_short_name` inside `owner_scope(authority)`. The reply includes `session_key` and `title`.

## Code

- `tui_gateway/methods_session.py`: legacy profile-scope decorator.
- `agent/title_generator.py`: warning-level failure reporting.
- `gateway/session_llm_oneshot.py`: authority handler, authorization and deferred owner-scoped work.
- `tests/hermes_cli/serve_handler_ledger.json`: records the RPC's authority ownership.

## Tests

Source tests inspected; **not executed in this mirror pass**:

- `tests/tui_gateway/test_multi_profile_hosting_fail_closed.py`: `test_session_title_suggest_runs_under_the_requested_profiles_scope` checks an explicit secondary profile and the launch-profile default under multiplexing.
- `tests/agent/test_short_name_suggest.py`: `test_suggest_fails_open` checks the `None` fallback and warning text.
- `tests/gateway/test_session_title_suggest.py`: `test_title_suggestion_uses_owner_transcript_and_secrets` is parameterized over both input keys and string/`None` suggestions. It dispatches through `AuthorityConnection`, checks the owner's home, secret and transcript, preserves the stored title, and checks missing parameters, missing sessions and denied permissions.

Tests replace the model call; no live provider request or Desktop rename-dialog interaction was performed here.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. These patches are not standalone.

## Gotchas

A null title is valid, not proof of a transport error. Suggesting and saving a title are separate operations. Profile scoping must surround deferred work itself. Invalid targets and permission failures must not reach the model call.

## Could this be a plugin?

A plugin could offer an alternative naming UI, but the built-in RPC's authority routing, authorization and credential scope need core changes.
