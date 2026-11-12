# Quota self-healing compatibility audit

Candidate core: `18578d047b949d4dff5dc4216d54a0b2c97daf33`.

- `agent_runtime_helpers.py` preserves the extraction API, then selects a short request-bucket reset only with explicit request-bucket evidence. Rejected unified usage windows retain existing behavior.
- `credential_pool.py` adds model generation metadata, delegates persisted-row adoption to a sibling helper, and resynchronizes externally cleared model cooldowns. Selection wrapper signatures remain unchanged. Borrowed runtime tokens remain memory-only; scoped verified clears use identity/generation comparison.
- No plugin selection code changed, so the fallback plugin's selection-code pin does not change. Existing core digests are retained for mixed rollout.

Verification so far: `check_compat.py --core <candidate>` passes. Isolated candidate account-usage suite with full core test-environment interpreter: **302 passed, 72 subtests passed**. Initial generic test-all interpreter run passed fast-rate-limit-fallback but account-usage's three import-isolation probes lacked ruamel; this is why full core test-environment is required.

Pending before publication: parent-core isolated suite and combined account-usage/fallback suite using full test-environment interpreter. Recheck digests if candidate changes.
