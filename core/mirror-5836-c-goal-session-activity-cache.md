# Goal session activity: source-signature caching (mirror 5718–5719)

## Behavior
- Goal activity replaces a global 2-second TTL and repeated profile glob/realpath walks with bounded per-database answer caches qualified by main/WAL/SHM identity and SQLite WAL generation.
- Changed or failed sources invalidate before reread; a write racing the read prevents caching that result, but the current call still returns the read result. WAL index generation is used rather than stat-only identity, rollback/unattested stores are not certified, and profile names come from one scandir.

## How it works
`hermes_cli/goal_session_activity.py` uses the existing lock-safe WAL generation reader.

## Code
- `hermes_cli/goal_session_activity.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_goal_session_activity.py::test_equal_stat_metadata_never_hides_committed_changes` checks WAL generation vs stat-only reuse; `test_directory_discovery_recovers_without_metadata_change` checks names-only discovery; `test_write_during_read_is_not_certified` checks that a racing result is returned but not cached. `test_reads_close_connections` checks connection lifetime. End-to-end UI goal display is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Stat metadata alone is not the cache authority. The final implementation scans profile names on each discovery and caches answers only for attested WAL generations.

## plugin?
No. Core change.
