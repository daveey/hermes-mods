# Desktop wake burst and board snapshot cost

Patches:

- `patches/0995-perf-backend-stop-a-Desktop-wake-burst-queueing-on-t.patch`
- `patches/1055-perf-kanban-throttle-GET-board-snapshot-refresh-to-o.patch`
- `patches/1066-perf-status-cache-parsed-large-YAML-on-disk-by-conte.patch`
- `patches/1071-fix-kanban-admit-board-reads-per-board-so-a-cold-boa.patch`
- `patches/1075-fix-kanban-cap-concurrent-board-reads-at-2-not-4-dav.patch`

A wake burst of session, kanban and status requests ran 6-9 s on a loaded Mac and tripped the Desktop's 5 s timeouts; a busy board re-read cost about 1.3 s CPU per poll.

## Behavior

`/api/status` caches the `_config_version` parse on the file signature (errors still re-read, warn and raise); `stale_ok` reaches `_ledger_owners` so the session strip no longer waits on the links lock; `GET /board` refreshes its snapshot at most once per 20 s.

## How it works

Caching and a parameter pass-through; no API change.

## Code

`hermes_cli/config_version_stamp.py`, `kanban_chat_links.py`, `kanban_session_activity.py`, `plugins/kanban/dashboard/board_snapshot.py`.

## Tests

`tests/hermes_cli/test_config_version_stamp_cache.py`, `test_kanban_chat_links.py`, `tests/plugins/test_kanban_board_read_cache.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.

## Follow-up patches (1066, 1071, 1075)

- **1066** (`61335886fdf`): `hermes_yaml.load` keeps parsed documents of 16 KB or more on disk under `<hermes root>/cache/yaml-parse/`, keyed by sha256 of the exact bytes plus the parser version (`marshal` payload; only plain str/int/float/bool/None/list/dict round-trip, so a document with other types such as timestamps is not cached; 256 entries, oldest evicted, atomic replace). `gateway/config_loader.py` and `utils.py` stop re-parsing `config.yaml` per `/api/status`. Cold first-burst status 2.7-3.1 s -> 0.6 s. Tests: `tests/test_hermes_yaml.py` (13), `tests/gateway/test_config_loader_no_reparse.py` (1).
- **1071** (`eb8969927d2`): every board read waited on one `asyncio.Semaphore(1)` in `web_read_coalescing.py`, so a cheap read of one board queued behind a cold read of another (the runners board hit the Desktop's 6 s timeout). `coalesced_read` gains `admission_key` / `max_concurrent`: one in-flight read per group, at most N groups at once; `_read_board` groups by board, `/api/profiles` keeps one slot. Tests: `tests/hermes_cli/test_web_read_coalescing.py` (5), `tests/plugins/test_kanban_read_admission.py`.
- **1075** (`22df58c65a6`): N is 2, not 4. Hub cold-start A/B: the default board's first read timed out in 2/5 runs at 4 (GIL contention with the other boards' reads) vs 0/10 at 2. Test: with three cold boards only two reads run until one finishes (`test_kanban_read_admission.py`, 3 tests in total).

Gotcha: the YAML cache is shared by profiles under the default hermes root; deleting it is always safe. Tests were added or changed by these commits; they were not re-run for this mirror.
