# Quiet extension reporting and measurement custody

Mirror batch 6; ordinals 4009, 4010, 4011, 4050, 4051.

## Behavior

Quiet observations distinguish explicitly identified external extension-code unknowns from blocking core/dependency failures, while retaining full-process CPU accounting. Admission diagnostics are not a quietness verdict. PTY and CPU-reader fixture changes strengthen the existing custody contracts.

## How it works

Attestation latches provenance at emission and partitions only four kinds: unrooted execution, unrooted modules, generated-origin unknowns and native loads. Core aliases, missing provenance, generic blocking emissions and failed native receipts remain blocking. Native-loader search names are not external provenance: only absolute paths may qualify. Responses expose a sorted report with total count, full-list digest and at most 20 entries. The observer retains the final report without subtracting extension CPU. Window freshness follows a 600-second publisher interval plus 60-second jitter; preflight retains its startup margin. Batching initially needs the maximum lease plus safety margin; subsequent samples use the monotonic remaining lease, so an unchanged admitted horizon does not become a collision solely through elapsed time.

## Code

Source paths touched by this feature group (including regression fixtures):

- `docs/quiet-extension-reporting.md`
- `gateway/quiet_control.py`
- `hermes_cli/code_attestation.py`
- `scripts/observe_gateway_quiet.py`
- `tests/gateway/test_quiet_control.py`
- `tests/gateway/test_quiet_extension_report.py`
- `tests/hermes_cli/test_code_attestation_quiet_policy.py`
- `tests/hermes_cli/test_code_attestation_relaunch.py`
- `tests/hermes_cli/test_kanban_quiet.py`
- `tests/hermes_cli/test_quiet_pty_custody.py`
- `tests/scripts/test_observe_gateway_quiet.py`
- `tests/scripts/test_observe_gateway_quiet_extensions.py`

Mail entries in the ordered series:

- 4009: [`2e209ccb60b4c63a7c57d27a0cf405472b306177`](patches/4009-daveey-report-unattested-extension-code-in-quiet-observations.patch)
- 4010: [`8733af4dde96d3f18d63514b693fe41c460bd8f0`](patches/4010-daveey-preserve-admitted-batch-horizon-as-quiet-lease-elapses.patch)
- 4011: [`b5bb8772b9ba56301fb0b782339a3e9e929edfde`](patches/4011-fix-quiet-keep-unresolved-native-loader-provenance-blocking-daveey.patch)
- 4050: [`d4af0e12c801d163cd32ee22de1ab8f3a9e082f3`](patches/4050-test-await-PTY-EOF-custody-before-closing-test-client-daveey.patch)
- 4051: [`2ba8ec415a369c0ed2f9e227bebfa8dba5f09602`](patches/4051-daveey-Test-named-PID-CPU-reads-with-controlled-work-deltas.patch)

## Tests

- `tests/gateway/test_quiet_extension_report.py`: real managed-bootstrap plugin/dataclass/absolute-ctypes integration checks report digest, truncation and check/start/snapshot/end responses; dirty core and a core-resident soname refuse. Window matrices compare gateway and host policy, elapsed-lease cases distinguish a preserved horizon from a one-second safety breach, and mid-capture holds/draining invalidate.
- `tests/hermes_cli/test_code_attestation_quiet_policy.py`: raw evidence survives partitioning; core symlinks, duplicate blocking marks, unknown origins/kinds and failed dependency receipts never downgrade.
- `tests/gateway/test_quiet_control.py`, `tests/hermes_cli/test_code_attestation_relaunch.py`, `tests/hermes_cli/test_kanban_quiet.py`: adjusted near-window/stale fixtures preserve refusal, preflight and cooldown assertions under the new freshness policy.
- `tests/scripts/test_observe_gateway_quiet_extensions.py`: reports survive pass/threshold/work/PID verdicts without excusing failures or subtracting CPU.
- `tests/hermes_cli/test_quiet_pty_custody.py`: the real legacy PTY handler consumes data and EOF with a reservation active, then releases while the WebSocket test portal is still alive.
- `tests/scripts/test_observe_gateway_quiet.py`: ready/work/done handshakes compare busy versus idle child CPU deltas, with stable start identity and unconditional reap; unequal lifetime totals are no longer used as identity proof.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

An informational report is not extension attestation. Linux libc/proc-map integration cases have platform prerequisites. PTY changes are test synchronization, not a production cleanup fix. No live lease, quiet-performance result or deployment is established here.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
