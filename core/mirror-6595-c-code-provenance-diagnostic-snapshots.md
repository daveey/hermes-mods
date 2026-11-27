# Bounded private code-provenance diagnostic snapshots

**Scope:** Passive private capture/page verb, collector, progress phases, dependency-unit sizing.

**Source ordinals:** 6407, 6408, 6409, 6410, 6411, 6412, 6413, 6468, 6469, 6593

## Behavior

- A passive private protocol verb exposes bounded, immutable, paged snapshots of the installed ledger (roots, file maps, root-qualified receipts, partitions), with SHA256-verified counts and a bounded authenticated collector.
- Capture has a caller deadline, bounded ledger-lock acquisition, encoded byte/page caps and token lifetime; a blocked filesystem syscall cannot be forcibly cancelled, so global single-flight custody persists past timeout until work ends. Unexpected ordinary worker exceptions settle as sanitized failures; BaseException is outside that catch contract.
- Admitted captures record fixed scalar phase/elapsed/worker-CPU samples; progress snapshots use a nonblocking recorder lock and return a fixed unavailable sample on contention. These are last-sampled lower bounds, not exact timeout-time CPU measurements.
- Dependency-unit retention is sized from measured inventory with its own larger bound and reused path views.

## How it works

- `gateway/code_provenance_control.py` registers at bootstrap; `hermes_cli/code_attestation_snapshot.py`, `code_provenance_client.py`, `code_provenance_progress.py`, `code_attestation.py` provide capture, client and phases.

## Code

Files touched by the series (paths relative to the source tree):

- `gateway/code_provenance_control.py`
- `gateway/control_dispatch.py`
- `gateway/control_socket.py`
- `gateway/run_bootstrap.py`
- `hermes_cli/code_attestation.py`
- `hermes_cli/code_attestation_snapshot.py`
- `hermes_cli/code_provenance_client.py`
- `hermes_cli/code_provenance_disk.py`
- `hermes_cli/code_provenance_progress.py`
- `website/docs/developer-guide/code-provenance-control.md`
- `website/docs/developer-guide/gateway-internals.md`

## Tests

Source test files changed or added by the series:

- `tests/gateway/test_code_provenance_control.py`
- `tests/gateway/test_code_provenance_custody_guards.py`
- `tests/gateway/test_code_provenance_guards.py`
- `tests/gateway/test_code_provenance_large_inventory.py`
- `tests/gateway/test_code_provenance_phase_boundaries.py`
- `tests/gateway/test_code_provenance_progress.py`
- `tests/hermes_cli/test_attestation_module_resolution.py`
- `tests/hermes_cli/test_code_attestation_snapshot.py`
- `tests/hermes_cli/test_code_provenance_client.py`
- `tests/hermes_cli/test_code_provenance_disk.py`
- `tests/hermes_cli/test_code_provenance_import.py`
- `tests/hermes_cli/test_code_provenance_progress.py`
- `tests/hermes_cli/test_code_provenance_projection_guards.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6407-6593, 10 commits: 6407, 6408, 6409, 6410, 6411, 6412, 6413, 6468, 6469, 6593) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Existing authorisation predicates are unchanged; nothing repairs or enrolls origins.
- Imports are inert and tests pin that.
- The snapshot exports retained metadata from the installed bootstrap ledger, including retained execution-unit/native-load records; it is not a disk census or complete historical execution proof. Fresh disk HEAD metadata is reported separately and proves neither a clean worktree nor boot identity. Dependency-unit overflow stays sticky and fail-closed; only the unit bound is enlarged (other report bounds and byte/installer verification unchanged).
- The sizing/path-view change is an evidenced operation-count improvement; the source does not attribute the whole historical latency to it.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Provenance lives in gateway bootstrap and the control socket.
