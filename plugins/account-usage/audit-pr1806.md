# Pre-audit: core PR #1806 (wt/gateway-quiet-v3, head b07a0bd43db9)

Card t_01a1d277c3f10cf1. Merge base = fork main 77a71becc9e6.

Drifted pinned file: `run_agent.py` 0b079f3feca5 (main, kept) -> 563b43f5894e (PR head, added).
No other file in the 104-file diff is pinned by account-usage or fast-rate-limit-fallback.

`git diff origin/main b07a0bd43db9 -- run_agent.py` (+33/-8) only touches background review spawning:
`_spawn_background_review_now` now reserves a `local_work_agent` custody token and delegates to the
new `_spawn_background_review_run` (the old body), whose thread releases the token after the
requeue. `agent/local_work_agent.py` is a per-home work registry with no credential, provider or route state.

Seams selection.py relies on (client factories, recover_with_credential_pool,
_failed_credential_identity, switch_model, interruptible_*_api_call, perform_api_call): untouched.
No new credential/route fence. selection.py unchanged, so recovery_wrapper and
fast-rate-limit-fallback/account-usage-compatibility.json need no refresh.

## Re-audit: head efeeea0fe583 (card t_01a1d2ad1d851226)

Merge base = fork main 3e6f01049555; main `run_agent.py` is still 0b079f3feca5 (kept).
Drifted: `run_agent.py` -> 54ec627c4261 (added; 563b43f5894e kept for hosts that saw the old head).
Of the 112-file diff, no other file holding an account-usage or fast-rate-limit-fallback pin
(file sha256 or function AST digest) changes.

`git diff origin/main efeeea0fe583 -- run_agent.py` (+34/-7) is the same background-review custody
change as b07a0bd43db9, plus: the delegate is called through the class
(`AIAgent._spawn_background_review_run(self, ...)`, so stand-in agents drive it) and a
`health: allow HX012` comment on the Thread start. Seams selection.py relies on: untouched.
Other credential-adjacent files in the diff: `agent/auxiliary_client.py` only adds an
`aux_call_scope` local-work decorator to call_llm/async_call_llm, moves `_AuxProbeClientStub` to
its own module and spawns the protected provider thread via `spawn_context_thread`; selection.py
wraps none of these, and none reads or changes credential/route state. No new fence.
selection.py unchanged.
