# Session scan admission and lock-safe cache stamps

Patches **2157–2158, 2178**. Extends the read-admission background in [backend-burst-perf.md](backend-burst-perf.md), without changing that historical document.

## Behavior

- HTTP bursts to `/api/profiles/sessions`, `/api/profiles/sessions/sidebar`, `/api/profiles/projects/tree` and `/api/sessions` queue expensive scans before taking shared HTTP worker slots. Unrelated status requests retain a worker during a blocked scan.
- Fresh sidebar/project-tree cache hits bypass unrelated misses. `/api/sessions` also checks completed cached results before scan admission; reads for another profile can proceed independently.
- Session-list WAL stamps no longer open and close `state.db-shm` on each read, preserving SQLite's process-held WAL locks during a concurrent write transaction.

## How it works

2157 registers async `coalesced_read` wrappers while retaining synchronous functions for direct callers and their existing DB locks. Session-list admission groups by profile with `max_concurrent=2`. 2158 adds `SingleFlightCache.peek`: only a matching non-null data stamp inside the TTL returns a copied completed payload; it never joins an in-flight computation. `_sessions_http` binds the ordinary handler defaults and checks that cache in the thread pool before joining admission. Ordinary validation remains in the handler.

2178 uses `sqlite_safe_read.pread_lock_safe` for the two WAL-index header copies. Its cached descriptor is not closed: closing any descriptor for a POSIX-locked file can release this process's locks on that file. A failed safe read returns no stamp rather than treating an unknown stamp as a cache hit.

## Code

`hermes_cli/web_routers/{profiles,sessions}.py`, `hermes_cli/session_list_flight.py`.

## Tests

**Upstream-supplied regression coverage, inspected in the patches; not executed in this mirror documentation pass.**

- `tests/hermes_cli/test_profile_scan_http_admission.py::test_profile_scan_burst_keeps_status_responsive` exercises all four HTTP routes through ASGI, a two-token worker pool, distinct request limits, warm hits, another session profile, and existing limit/archive validation.
- `test_session_list_peek_requires_fresh_matching_stamp` in that file checks copy isolation, mismatched/missing stamps, disabled TTL and expiry.
- `tests/hermes_cli/test_session_list_flight.py::test_data_stamp_keeps_this_process_wal_locks` holds a real SQLite write transaction, repeatedly reads its stamp, and checks that a separate process still cannot acquire the writer lock. This tests the stamp helper, not a complete HTTP write/read race.

## Applying

Use the full ordered replay in [README.md](README.md), including its replay plan and integration bridges. These patches are not independent application units.

## Gotchas

This is admission before expensive work, not a dedicated pool for every request: cache probes still use the HTTP thread pool. A warm hit must pass both stamp and age checks. No live latency, corruption-repair or deployment result is claimed here; the WAL change prevents the demonstrated lock release, not repair of an already damaged database.

## Plugin?

No. Route registration, cache admission and SQLite descriptor lifetime are core concerns.
