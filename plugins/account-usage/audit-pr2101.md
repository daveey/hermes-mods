# Pre-audit: core PR #2101 (kanban quota recovery proof wire, head cda584f9b175)

Card t_01a1d48cd34f2d41. PR base 7cd82b40a11a; fork main's `agent/anthropic_adapter.py` is identical to the base
(d40c5260063e), and a merge of the PR head onto current main yields the same file as the head.

Drifted pins (main digest kept, PR digest added):
- `execution-compatibility.json` `agent/anthropic_adapter.py`: d40c5260063e (main) -> ae3a9853cedc (PR)

The PR's other files (`hermes_cli/kanban_quota_detector.py`, `hermes_cli/kanban_quota_recovery.py`, tests) are not
pinned by account-usage or fast-rate-limit-fallback.

What changed in `agent/anthropic_adapter.py` (+14/-5), a pure extraction:
- New `claude_code_identity_headers()` returns the same `user-agent` (`claude-code/<version> (external, cli)`) and
  `x-app: cli` pair that was previously assigned inline.
- New `oauth_wire_headers(base_url)` composes the OAuth betas + identity headers for raw callers (the Kanban quota
  proof). Nothing in the agent request path calls it.
- `_build_anthropic_client_with_bearer_hook` (key_cmd OAuth) and `build_anthropic_client` (`style == "oauth"`) now
  use the helper; header keys, values and precedence are unchanged (identity headers still override after the
  beta header, as before).

Against selection.py's seams:
- Client factories: `build_anthropic_client` / the bearer-hook builder take the same arguments and send the same
  headers for every credential style, so `agent._build_anthropic_client_for_key(key)` (selection.py) builds the
  same client for a request-local snapshot key.
- `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`, `interruptible_*_api_call`,
  `perform_api_call`: untouched.
- No new credential or route fence: the change only names existing headers; no gate is added that a request-local
  snapshot could bypass.
- selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
  no refresh.
