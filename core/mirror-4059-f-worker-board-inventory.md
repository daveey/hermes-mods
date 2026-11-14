# Authoritative account-board worker census

Mirror batch 6; ordinals 4033, 4034, 4035.

## Behavior

Real-account worker inventory accepts canonical default/named/profile boards, not arbitrary scratch homes or symlink-imported locations. Admission uses that inventory authoritatively; unknown profile discovery errors remain unknown rather than silently reducing occupancy.

## How it works

Resolved board paths must fit canonical account shapes when using the immutable real policy root. Isolated explicit test roots retain fixture flexibility. Discovery covers root and profile installations and deduplicates records; legacy foreign entries are filtered read-only before missing-file checks. Admission passes board_paths to metrics so current-home discovery cannot re-add scratch data. Generic metrics discovery remains available when that argument is omitted. Explicit stat skips only vanished profile entries and propagates other I/O failures.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/machine_metrics.py`
- `hermes_cli/worker_account_boards.py`
- `hermes_cli/worker_resource_admission.py`
- `tests/hermes_cli/test_worker_account_boards.py`
- `tests/hermes_cli/test_worker_board_root_boundary.py`
- `tests/hermes_cli/test_worker_legacy_scopes.py`

Mail entries in the ordered series:

- 4033: [`2e3b1182859176b1dd1079f1d15d533f3a892eb7`](patches/4033-fix-worker-bound-real-inventory-to-account-board-roots-daveey.patch)
- 4034: [`558d5a3e6a2677b936df9c2cc1782c2a32d3efcb`](patches/4034-fix-worker-use-authoritative-account-boards-for-slot-census-daveey.patch)
- 4035: [`c536c1baa61b6c2f22817d8acaae0a4ea8adfb76`](patches/4035-fix-worker-preserve-unknown-profile-discovery-errors-daveey.patch)

## Tests

- `tests/hermes_cli/test_worker_board_root_boundary.py`: scratch/foreign/nested/symlink paths never enter registration/census; canonical root/named/profile boards do. Legacy foreign records, present or missing, stay ignored without deletion. Composed admission ignores malformed current scratch homes, discovers unregistered profiles, preserves missing real boards as unknown, honors an authoritative empty inventory and propagates profile permission/I/O failures.
- `tests/hermes_cli/test_worker_account_boards.py`: the real-account pytest refusal fixture now uses a genuinely canonical board so path filtering cannot mask the isolation guard.
- `tests/hermes_cli/test_worker_legacy_scopes.py`: census wiring passes the exact authoritative inventory to metrics while unknown reservation behavior stays covered.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

HERMES_HOME alone cannot authorize a real-account inventory location. Propagated inventory errors are not zero occupancy. No live census or host deployment is asserted.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
