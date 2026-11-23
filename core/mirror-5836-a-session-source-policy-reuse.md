# Session listing: reuse source-policy config reads (mirror 5534–5536)

## Behavior
- `automated_session_sources` now projects `automated_sources`, `interactive_sources` and `sidebar_profiles` from one `_sessions_source_list` read of the home's config (previously three separate reads); the root home's `sidebar_profiles` is read separately only when the root differs from the home.
- The helper's docstring says it retains no config between calls, so edits are seen on the next call (tested for file, env and root edits). An HTTP test compares include/exclude payloads with a `_legacy_policy` reimplementation; "byte-identical" is not independently established beyond that test.

## How it works
`hermes_cli/session_listing.py`.

## Code
`hermes_cli/session_listing.py`.

## Tests
Source: `test_session_source_policy_reuse.py` (by test names: each home read once with the stamped payload preserved; fresh policies observe file, env and root edits; errors restore the home and do not memoize the fallback; an unreadable config fails open for single and multi key; a default home unlisted in its own `sidebar_profiles` is automated; a non-dict `sessions` section fails open), `test_session_source_policy_http.py` (include/exclude response bytes compared with the prior algorithm). Not executed in this mirror pass.

## Applying
Apply in order.

## Gotchas
Behavior on a missing or malformed `sessions` section remains fail-open (empty lists), as tested.

## plugin?
No.
