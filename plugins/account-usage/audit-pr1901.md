# Pre-audit: core PR #1901 (Upstream sync 440525c36f, head 440525c36f80)

Card t_01a1d37a0d4426e4. Fork main at audit time: 3cde49def6e3 (merge base 2a2e0292f947).

Drifted pins (main digest kept, PR digest added):
- `execution-compatibility.json` `run_agent.py`: 0b079f3feca5 (main) -> 2723695bbcbe (PR)

No other account-usage pinned file differs between main and the PR head.

What changed in `run_agent.py` (+6/-2, from fork commit 4fd509e4e0 "hosted scope is deny-by-default"):
- `_get_session_db` (lazy state-DB open) also returns `None` when
  `tools.hosted_tool_policy.host_feature_enabled("session_recall")` is false.
- `_spawn_background_review` returns early when `host_feature_enabled("background_review")` is false.

Against selection.py's seams:
- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`,
  `interruptible_api_call` / `interruptible_streaming_api_call`, `perform_api_call`: untouched.
- The new gates are host-feature switches on session recall and the background review fork; neither reads or
  writes credential, pool or route state, so no request-local snapshot can bypass them and no new
  credential/route fence is introduced. selection.py does not reference either method.
- selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
  no refresh.

Verification (worker-7, Linux):
- `check_compat.py` exit 0 on installed core 626569961e47 and on the PR head 440525c36f80.
- `scripts/test-all -k account-usage` green on the installed core and on the PR head, with
  `HERMES_MODS_PYTHON` = the install's committed PM venv (3.14.9). With the bare store Python, test-all's
  isolated HOME makes core bootstrap refuse (`no dependency environment is committed`), unrelated to this pin.
