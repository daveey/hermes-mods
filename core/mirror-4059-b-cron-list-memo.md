# Cron list memoizes normalized job records

Patches: 3835.

## Behavior

Repeated job listings avoid reparsing unchanged jobs files, but mutations, replacement files and live execution metadata remain visible on subsequent reads. Returned nested records cannot mutate the cache.

## How it works

`_listed_records` keys each profile/store path by the file's mtime nanoseconds, size and inode. A matching stamp returns a deep copy and records the load stamp; a miss loads/normalizes records and remembers a deep copy only if a second stat confirms no write raced the read. `list_jobs` retains the later live/latest-execution overlay outside the memo. `cron.list_jobs` accounting marks memo hits as skips.

## Code

- `cron/jobs.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/cron/test_list_jobs_memo.py` (patch 3835).

- **3835:** Repeated lists do not reparse, rename/add/delete and inode replacement refresh immediately, caller mutations cannot corrupt nested records, execution metadata stays live, profile stores isolate and memo hits increment accounting skips.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The memo is a stat-stamp optimization, not a content-hash audit of adversarial same-stamp edits. Only normalized file records are cached: execution status must not be frozen with them. Per-store keys prevent profiles sharing the wrong jobs.

## Plugin?

No. This changes the core job-store listing implementation while preserving its caller contract.
