# Change-proportional cron execution list reads

Mirror snapshot 4059; covered ordinals: 3901.

## Behavior

`list_jobs` reuses a read-only execution-ledger reader rather than opening the ledger twice per unchanged list. A newly claimed/running/finished execution or another connection's commit is reflected on the next list, and owner death is rechecked even without a ledger write.

## How it works

`cron/executions.py:listed_executions` keeps per-file rows keyed by the connection's `PRAGMA data_version`, with a sixteen-view cap and explicit closing. It caches latest rows and all claimed/running rows, filters requested job IDs, re-evaluates `_row_owner_live`, and returns fresh row dictionaries. File or shared-memory identity changes drop the reader; missing files, SQLite failures, and Windows use the existing exact reads. Fork handling forgets inherited views without using or closing the parent's connections. `cron/jobs.py:list_jobs` uses this combined API; other exact-read callers remain unchanged.

## Code

- `cron/executions.py`
- `cron/jobs.py`

### Mirrored commits

- 3901: `c8246829a65411e36f9f531832baf33c8ba3a527` — Memoizes cron execution list rows on a kept reader data version.

## Tests

- `tests/cron/test_list_jobs_executions_memo.py`: unchanged lists cause no additional SQLite opens; starts, running transitions, finishes, foreign writes, owner death, and file replacement appear on the next list. Caller mutation cannot poison results; the combined API matches exact reads; no-ledger fallback still creates the ledger; loop-accounting counters distinguish reuse from recomputation.
- `tests/cron/test_list_jobs_memo.py`: the jobs-file memo still asks for latest/live execution answers via the new combined API on each call.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

`data_version` belongs to one connection and cannot be compared across freshly opened readers. Owner liveness is deliberately outside the memo. Windows uses exact reads to avoid pinning files against delete/rename; these added tests do not establish a Windows performance improvement.

## Plugin?

No. This is an internal ledger read optimization with liveness-sensitive semantics.
