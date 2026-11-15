# Durable flaky-CI intake, bounded manifests and fair retries

Patches: **4265, 4269, 4285–4286, 4300–4302, 4334** (4352 snapshot).

## Behavior

Intake requires exact final comment readback before acknowledgment. Ambiguous creates and stale owner caches recover conservatively without duplicate card creation or treating unreadable inventory as authoritative absence.

Large owner diagnostics remain available by immutable content-addressed reference. Daily manifests batch work and bound terminal history; owner-resolution and page-retry queues share durable FIFO fairness across restarts.

## How it works

Owner-check JSON above 1024 UTF-8 bytes is stored atomically in owner_check_details with its SHA256/count reference. Small payloads remain inline. Exact job URL and explicit job identity fence receipt recognition.

Retention preserves pending/recent and foreign-repository history, grants legacy rows their retention window, and uses indexed primary-key cleanup. Nightly dependency pins are checked. Both retry lanes merge by durable tickets before external work and rotate per attempt, avoiding starvation when a slow operation overruns a phase budget. Batch flushes commit pages before delivery and honor drain deadlines.

## Code

- `docs/ci-flaky-intake.md`
- `scripts/ci/flaky_intake.py`
- `scripts/ci/flaky_kanban.py`
- `scripts/ci/nightly_e2e.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/ci/test_flaky_intake.py`
- `tests/ci/test_flaky_intake_cross_queue.py`
- `tests/ci/test_flaky_intake_diagnostics.py`
- `tests/ci/test_flaky_intake_liveness.py`
- `tests/ci/test_flaky_intake_recovery.py`
- `tests/ci/test_flaky_intake_retention.py`
- `tests/ci/test_nightly_e2e_pin.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

An owner_check_ref is not an empty inventory or disposition. Backups must include referenced detail payloads, and references must not be pruned independently. A timeout is not authoritative create absence. No real CI owner, comment or issue was changed in this pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
