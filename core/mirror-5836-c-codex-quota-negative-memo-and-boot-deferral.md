# Workers: negative quota memo, receipt coalescing, boot-time recovery deferral (mirror 5684–5686, 5691, 5730–5731)

## Behavior
- A cross-process negative-only memo avoids repeating a failing quota probe for the smaller of the caller interval and 60 seconds; it is scoped to token, endpoint, reset and failure generation. Only negative results are persisted; missing, invalid, stale, future or nonfinite memo timestamps miss. Nonfinite reset/failure inputs are hashed without raising; they are not an unconditional cache bypass.
- Same-scope session metadata ACK clears are coalesced into the next durable pending write; child, transcript, compression and terminal receipts still clear synchronously.
- During authenticated managed Kanban construction, Codex quota recovery returns fail-closed without network and a daemon refresh is requested after scope exit against a separately loaded pool, deduplicated per home while one is running. If fast selection fails, inline recovery is retried for a typed Codex wall; other providers keep ordinary recovery.

## How it works
`hermes_cli/auth_codex_quota_negative.py` implements the memo; `agent/runtime_session_store.py` coalesces receipts; `agent/codex_boot_recovery.py` and `agent/managed_worker.py` defer recovery.

## Code
- `hermes_cli/auth_codex_quota_negative.py`
- `hermes_cli/auth_codex.py`
- `agent/runtime_session_store.py`
- `agent/codex_boot_recovery.py`
- `agent/managed_worker.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_auth_codex_quota_negative_memo.py::test_negative_memo_is_shared_by_real_subprocesses` checks the negative memo with a stub probe. `test_auth_codex_quota_negative_fail_safe.py` checks invalid stamps, write failures and caps. `tests/agent/test_runtime_session_receipt_coalescing.py` and `tests/hermes_state/test_runtime_worker_receipt_coalescing.py` check coalescing/exact replay. `tests/agent/test_codex_boot_recovery.py::test_background_start_is_nonblocking_and_deduplicated` and `test_codex_boot_recovery_fallback.py::test_recovery_survives_short_lived_process` cover deferral and inline fallback with fixture pools/probes. Live provider recovery is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- An exhausted account stays exhausted under a memo hit; never persist positive recovery.
- A usable-only construction requests no refresh; thread-start failure does not grant positive quota evidence.

## plugin?
No. Core change.
