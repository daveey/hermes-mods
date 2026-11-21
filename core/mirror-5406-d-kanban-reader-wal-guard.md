# Kanban: kept readers across restore publication and refused close

Mirror snapshot 5406; ordinals 5391, 5392.

Entries:

- **5391**: fix(kanban): track kept observers across offline restore publication
- **5392**: fix(kanban): retain WAL guard after refused reader close

## Behavior

- **5391**: restore tracks real in-process readers via the holder registry, reserves missing-target imports against kept readers and clears stale sidecars only after refusal/reservation.
- **5392**: after a refused thread-checked close the guard is rearmed from the verified WAL path without SQL.

## How it works

- `backup_restore.py`, `kanban_lockguard.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/backup_restore.py`
- `hermes_cli/kanban_lockguard.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_cached_ro_restore_safety.py`
- `tests/hermes_cli/test_kanban_observer_tracking.py`
- `tests/hermes_cli/test_kanban_restore_concurrency.py`
- `tests/hermes_cli/test_kanban_open_session_isolation.py`
- `tests/hermes_cli/test_kanban_reader_close_isolation.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Correctness only; no performance claim.

## Why core instead of a plugin

Board storage guards are core.
