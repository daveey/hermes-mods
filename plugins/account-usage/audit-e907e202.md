# Execution compatibility audit: fork core e907e2023ca

The core-backed suite at mods 2a2fd3f3 failed closed on five whole-file
execution pins. This refresh adds audited digests without removing older versions;
selection.py, request factories, middleware and fallback composition pins are unchanged.

## Audited deltas

Compared each file with its most recent revision whose SHA-256 is already accepted:

- `agent/turn_api_call.py`, since `1a652983ea16`: `perform_api_call` now serves a
  restored provider-retry deadline before middleware or SDK dispatch. Yield/redirect
  exits send no request and consume no pending selection. Its frame and signature
  remain the execution boundary used by `calling_agent()`.
- `agent/agent_runtime_helpers.py`, since `d4f5974c9d56`: vendor reset-header parsing
  moves unchanged into `agent_runtime_helpers_error_context.py`; `set_error_code`
  adds `details.error_code` to the extracted error context. The selected-recovery
  adapter still forwards that context while explicitly identifying the snapshot's
  credential id and model. The new `credits_required` bench therefore applies to
  the selected credential rather than silently adopting the agent's credential.
  Wrapped recovery, failed-identity and route-switch functions are unchanged.
- `run_agent.py`, since `215f319d0aa2`: background-review work owns an independent
  drain lease until its thread cleanup finishes. Profile context propagation and
  provider dispatch remain unchanged; this is not a new account-selection path.
- `agent/turn_recovery.py`, since `8fa054f240b9`: the backoff helper accepts an
  absolute deadline and opt-in update yield. Ordinary callers retain the old
  wait/Stop/redirect contract. No credential mutation is introduced.
- `agent/turn_api_error.py`, since `8fa054f240b9`: API-error backoff can yield only
  after its absolute deadline is durably stamped. Failure to stamp retains ordinary
  sleeping. The restored wait is outside credential dispatch; it cannot substitute
  another account or leak a request-local snapshot across turns.

`provider_retry_deadline.py` was also inspected: missing callbacks are inert,
restored waits run before provider work, and turn cleanup resets deadline state.
The fallback AST pins and selection composition SHA still match without changes.

## Cause and detection

The failures cascade from `check_pins()` refusing adapter installation when core
files no longer match an audited digest, not from the unrelated Fable usage column
added by mods 2a2fd3f3. Whole-file pin drift is intentionally recurring: each new
core version needs a manual semantic audit rather than automatic trust. Public
no-core CI cannot exercise this contract; the core-backed suite gate detected it
and opened the repair card. Preserve that fail-closed behavior and existing drift
and foreign-wrapper regression tests.

## Verification

Python 3.14.9, frozen fork core `e907e2023ca`:

- Before refresh: canonical account-usage suite failed (102 failures, 247 passes).
- After refresh: `scripts/test-all --hermes-core <core> -k plugins/account-usage`:
  account-usage **341 passed, 92 subtests passed, zero skips**; repo-guards
  **50 passed, 103 subtests passed, two platform SQLite skips**.
- `scripts/test-all --no-core --exact -k plugins/account-usage`: PASS, with
  core-dependent tests skipped as required for public CI.
- Independent bounded semantic audit by Claude Opus 5.5 approved the five additive
  digests. This is audit evidence, not a live gateway reload or production-fault proof.

Rollback: revert the additive pin refresh; selection then refuses this new core
again while older audited versions remain supported.
