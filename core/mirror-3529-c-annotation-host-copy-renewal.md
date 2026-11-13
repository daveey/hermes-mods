# Annotation host copy: per-profile demand, early renewal, disk copy v2

Patches: **3528**.

## Behavior
- In a serve hosting several profiles, the holder now refreshes scopes demanded on non-launch profiles.
- The holder renews the host copy before adopters would miss it (copy age stays at or under about 22 s in the test) instead of waiting for the 30 s cache expiry.
- Disk copy version moves 1 to 2; version-1 copies are ignored (one cold start), so an older encoding is not seeded as data.

## How it works
- 3528 moves the demand check into `_holder_refresh`, inside the scope's own context. When the copy has aged past the renew threshold the holder sets `_revalidate.force_read.scope`, making `counts_fresh` refuse the cache for that scope on that thread so a real read publishes a fresh `read_at`. `_VERSION` is bumped because lists/dicts are now wrapped as `{l}`/`{d}`.

## Code
- `hermes_cli/kanban_annotation_disk.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_session_activity_revalidate.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_annotation_disk.py`: `test_a_version_1_copy_is_ignored`.
- `tests/hermes_cli/test_kanban_annotation_host.py`: `test_holder_refreshes_a_scope_demanded_on_another_profile`, `test_holder_renews_the_copy_before_adopters_would_miss`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Upgrading discards existing v1 disk copies once (one cold start).
