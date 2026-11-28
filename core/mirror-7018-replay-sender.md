# Replay sender: journaled transport, deferred observation and bounded recovery

Patch coverage: 6842-6929 (all 88 ordinals: 6842, 6843, 6844, 6845, 6846, 6847, 6848, 6849, 6850, 6851, 6852, 6853, 6854, 6855, 6856, 6857, 6858, 6859, 6860, 6861, 6862, 6863, 6864, 6865, 6866, 6867, 6868, 6869, 6870, 6871, 6872, 6873, 6874, 6875, 6876, 6877, 6878, 6879, 6880, 6881, 6882, 6883, 6884, 6885, 6886, 6887, 6888, 6889, 6890, 6891, 6892, 6893, 6894, 6895, 6896, 6897, 6898, 6899, 6900, 6901, 6902, 6903, 6904, 6905, 6906, 6907, 6908, 6909, 6910, 6911, 6912, 6913, 6914, 6915, 6916, 6917, 6918, 6919, 6920, 6921, 6922, 6923, 6924, 6925, 6926, 6927, 6928, 6929).

## Behavior

A new Kanban sync *replay sender* sends board changes to the Board as a strict, journaled stream with exact custody, and a staged observation of "deferred" status handoffs. The source labels the early commits work-in-progress with no enrollment or activation, and later commit text and the added design notes (`docs/kanban-replay-sender-admission.md`, `kanban-replay-deferred-observation.md`, `kanban-replay-recovery-audit.md`, `kanban-replay-final-audit.md`, `kanban-replay-capture-storage.md`, a user-guide recovery page) keep migration/certification gates **dormant**: no writer gates are installed on a live board and nothing proves older processes have drained. Treat this as staged infrastructure, not an enabled feature. Precisely: the sender is already on the production push path. From 6845 `kanban_sync_client.push` is `kanban_replay_sender.push`, which selects the strict journaled transport only when `sync_state.journal_id` is present and otherwise calls the former body, renamed `_push_legacy`; this series contains no enrollment writer, so boards that are never enrolled keep the legacy transport (6903-6906, 6923-6925 pin that). What is not claimed is that any board is enrolled, that a Board serves the replay routes, or that older writers have drained. Changes that do touch live boards: the new sender-lock admission and bounded wait on legacy direct pushes (6923), the legacy-capability-probe 410 handling (6925), new schema column `kanban_sync_state.replay_restore_required`, rebuild hold logic in `kanban_db_recover`, and a new pinned dependency (`rfc8785==0.1.4`, Python >= 3.14) in `pyproject.toml`/`uv.lock`. A new operator command `kanban replay-resume --expect <op_id> --reason <text>` (6870, later widened to `resume_all`) explicitly retries paused replay work.

What the series establishes:
- **Wire and journal.** Canonical digest framing using the third-party `rfc8785` codec (imported by the sender/wire modules; the dependency marker is Python >= 3.14 while `kanban_sync_client` imports the sender at module load, so an interpreter without the package is a reviewer-noted, unexercised risk), a transactional width-1 single-slot allocator with exact wire custody, persisted journal binding and restart reconciliation (fail closed when stream identity or contiguous durable verdict cursors disagree; pending retry and predecessor ACK retained; successors carry the predecessor ACK; ACK prefixes preserved).
- **Admission.** A distinct nonblocking per-board sender lock is taken before transport selection and held through send, settlement and ACK; detach takes round then sender lock; contention raises a busy error without network wait or custody mutation. A paused sender does not stop healthy pulls on the same board.
- **Parent-first transport.** Ordinary rows go parent-first; rejected parent obligations are retained and dependents fenced; synthetic parent custody survives cache cleanup, feed echoes and lifecycle cleanup; board import is fenced against issued/rejected replay custody; sends route through pre-head fences; frozen controls and omission restores replay.
- **Deferred observation.** Final `deferred` verdicts are retained as bounded separate handoffs (not success), observed with bounded durable rotation and evidence episodes that do not count downtime; a Board advertising `deferred_observation` is required, and a missing capability never means an empty queue.
- **Recovery.** Strict transport recovery is bounded with durable exact resume; HTTP 410 recovery is bounded; lost anchors fail closed; overlapping faults resume atomically; recovery writers never invent capture provenance; stale protocol custody is never refilled from older backups; primary-side delete/ACK and episode-clock corrections are applied; capture exposure is observed before authenticated I/O.
- **Isolation.** Malformed Board bytes/JSON/Unicode, decoder resource limits, malformed envelopes and out-of-range scalar fields (exact int, not bool, signed 64-bit) fail only the owning board as typed errors, never replay an uncertain mutation, and keep healthy boards moving; current-row lease fields are validated before binds.
- **Legacy compatibility.** A never-enrolled rebuilt board is never held and never invents recovery loss; a legacy hub's 410 on the optional replay-capability probe means "no capability"; legacy direct pushes by never-enrolled boards skip the sender fence with a bounded wait.

## How it works

`hermes_cli/kanban_replay_sender.py` (largest module), `kanban_replay_journal.py`, `_wire.py`, `_values.py`, `_admission.py`, `_controls.py`, `_reconcile.py`, `_existence.py`, `_recovery.py`, `_primary.py`, `_reply.py`, `_deferred.py`, `_observation.py`, `_episodes.py`, `_migration.py`, `_repair.py`, `_diagnostics.py`, with `kanban_db_sync.py`, `kanban_db_recover.py`, `kanban_sync_client.py`, `kanban_sync_cli.py`, `kanban_sync_capture_{sites,storage}.py` and transport adapters (`kanban_protect_transport.py`, `kanban_retain_transport.py`, `kanban_actionability_transport.py`).

## Code

All of the above plus test support in `tests/hermes_cli/` (`kanban_capture_*_cases.py`, `kanban_replay_field_matrix_support.py`) and the design docs listed.

## Tests

Read from source; **not executed here**. Source coverage includes independent-process sender/detach tests against a loopback HTTP peer (controlled test infrastructure, *not* proof of a deployed receiver), writer-gate downgrade tests with a preopened connection, full-storage and hard-death custody tests, reopen-history models, malformed-reply audits, legacy capability/direct-push tests (`test_kanban_legacy_capability_status.py`, `test_kanban_legacy_direct_push.py`), creator-empty recovery. Commit-text counts are owner-reported. The real deployed Board, fleet drain and activation are **untested** and not claimed.

## Applying

Treat as one dependency unit, ordinals 6842-6929 in order after the prior series; later commits correct earlier ones heavily (for example 6903→6906 never-enrolled handling; 6899→6901 lost anchors).

## Gotchas

- Dormant: documentation in the source says local exclusion is not migration readiness; old code that does not take the new sender lock remains an external-drain problem.
- Never settle a deferred verdict without durable semantic custody.

## Why core, not a plugin?

Kanban sync client/store internals.
