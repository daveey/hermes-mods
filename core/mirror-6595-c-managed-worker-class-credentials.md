# Class-authorised credential staging for managed workers

**Scope:** Private credential staging uses eligible routes of the explicitly selected class and the effective launch model.

**Source ordinals:** 6402, 6457, 6458, 6459, 6460

## Behavior

- Managed worker environments stage inference credential names derived from the eligible routes of the explicitly selected provider class, not from legacy auto-detection, retaining billing/funding/provider-disabled gates and the execution-bound credential handle; no route is allocated while staging and concrete-provider auto-detection is unchanged.
- The effective launch model (from task policy) overrides only the primary credential descriptor; separately declared auxiliary and fallback routes are retained, and concrete/custom provider metadata is preserved.

## How it works

- `gateway/session_worker_credentials.py` derives names; `gateway/session_managed_worker.py` passes the effective model from bootstrap into staging.

## Code

Files touched by the series (paths relative to the source tree):

- `gateway/session_managed_worker.py`
- `gateway/session_worker_credentials.py`

## Tests

Source test files changed or added by the series:

- `tests/gateway/test_kanban_class_credential_boundary.py`
- `tests/gateway/test_managed_worker_class_credentials.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6402-6460, 5 commits: 6402, 6457, 6458, 6459, 6460) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Saved config is not mutated and the exec allowlist is not broadened; credentials never appear in bootstrap frames.
- Ordinary classes never gain another class's credentials merely because it is registered.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Credential staging happens in the gateway's worker launch before any plugin runs.
