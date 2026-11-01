# State-keyed profile session scans

## Behavior

Repeated HTTP `/api/profiles/sessions` polls with unchanged session databases reuse raw rows and totals rather than scanning again. A committed database write becomes visible on the next poll; there is no TTL. Query/filter variants remain separate. Concurrent identical polls share one scan under the existing serialization lock.

Config policy, profile enumeration, active-time tagging and activity annotations are recomputed for each request; they are not frozen with the cached database rows.

## How it works

`ProfileSessionScanCache` retains read-only SQLite observers, comparing `PRAGMA data_version` on the same connection together with database file identity. It checks the generation before and after scanning and does not publish a result across a concurrent write. Bounded LRU maps retain at most 64 profiles and 16 query variants per profile. Eviction, database replacement and errors close observers; callers receive copies, preventing annotations from contaminating later polls.

## Code

- `hermes_cli/profile_session_scan_cache.py`: observer/cache lifecycle.
- `hermes_cli/web_routers/profiles.py`: cache inside `_PROFILE_SESSION_SCAN_LOCK`; live response decoration outside it.
- Patches **1475–1476**, source commits `12f4138d509d41276a2646eed23590de2963adc1` and `5a499bea1cf0b92834466c17673027e93607742d` (PR #486).

## Tests

Implementation verification: RED unchanged HTTP polling performed two scans instead of one; GREEN **11 cache and serialization tests**, **253 related route tests**, and **15 check gates**. Exact PR head `846490274c2689fbc23afd21b29344a34fe364b4` passed CI run `41280652346` before normal merge.

- `tests/hermes_cli/test_profiles_sessions_scan_cache.py`: real SessionDB/HTTP unchanged polls and next-write visibility; filter variants; live annotation/time changes; actual config-file changes and profile roster changes; concurrent identical requests; a write during the scan.
- `tests/hermes_cli/test_profile_session_scan_cache.py`: committed versus rolled-back changes in WAL and DELETE journal modes; missing/replaced stores; observer closure on LRU eviction; mutation isolation and query eviction.
- `tests/hermes_cli/test_profiles_sessions_scan_serialization.py`: existing serialization contract remains unchanged.

Mirror replay is independently checked in [mirror-1476-verification.md](mirror-1476-verification.md). Native production performance acceptance is separate from these tests; this document does not claim a measured CPU reduction.

## Applying

Use the full bridge-aware mirror recipe in [README.md](README.md), with the 1476 policy/replay snapshot. These patches follow the existing serialization patch and are not an independent plugin.

## Gotchas

A write anywhere in an observed database invalidates its cached queries, including unrelated table writes. Busy session stores can therefore still require scans on every poll. Time-sensitive and file-derived response metadata stays outside the cache. Synthetic absent-home scan seams intentionally bypass caching.

## Plugin?

No. This changes the core route's database scan boundary and uses its existing process-local serialization lock.
