# Kanban quota recovery: OAuth proof wire and failed-proof cooldown

Patches: **3394**.

## Behavior
- The quota-recovery completion proof now sends the real OAuth client's wire headers (common betas plus OAuth-only betas, detected Claude Code user agent, `x-app`) and the shared Claude Code system prefix, so the proof cannot drift from the real client and report false "still walled" results.
- A failed proof cools down per account across starts; a verified proof releases the cooldown; a broken proof gate fails open.
- The drain skips quietly without flock and keeps its reports; an unreaped resolver timeout never escapes and completion returns false when the resolver cannot be reaped.
- `recover()` resolves the cached Claude Code version before its proof deadline starts.

## How it works
- 3394 adds `oauth_wire_headers()` and `claude_code_identity_headers()` to `agent/anthropic_adapter.py` (both OAuth paths use the latter), and updates `kanban_quota_detector.py` and `kanban_quota_recovery.py` accordingly.

## Code
- `agent/anthropic_adapter.py`
- `hermes_cli/kanban_quota_detector.py`
- `hermes_cli/kanban_quota_recovery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Across `tests/hermes_cli/test_kanban_quota_detector.py`, `test_kanban_quota_dns_deadline.py`, `test_kanban_quota_recovery.py`: `test_drain_without_flock_skips_quietly_and_keeps_reports`, `test_unreaped_resolver_timeout_never_escapes`, `test_completion_returns_false_when_resolver_cannot_be_reaped`, `test_proof_wire_headers_equal_the_oauth_client`, `test_failed_proof_cools_down_per_account_across_starts`, `test_verified_proof_releases_its_cooldown`, `test_broken_proof_gate_fails_open`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
