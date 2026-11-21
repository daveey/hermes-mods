# Plugin discovery: reuse and retry offline recall and tree metadata reads

Mirror entries: 5432, 5433, 5434.

## Behavior

- Plugin discovery reuses decoded offline removal lists within one discovery sweep instead of decoding them for every plugin gate. It adds no network request.
- Both the local live-catalog cache and the in-tree removal list are revalidated on each lookup. Replacement, deletion, recreation and catalog-directory changes invalidate reuse.
- Transient JSON, YAML or metadata read failures are not remembered as successful empty recall lists: a later lookup in the same sweep retries them.
- Forced discovery starts a new scope. Reads outside discovery retain their ordinary, non-memoized behavior, and callers cannot corrupt later decisions by mutating returned entries.

## How it works

`PluginManager.discover_and_load()` wraps its inner sweep in `removal_read_scope()`. A `ContextVar` holds a scope-local dictionary; token restoration in `finally` preserves an enclosing scope and removes the scope even after an exception.

`cached_removed_entries()` keys reuse by both input paths and their `file_cache_version()` results. Stored rows are immutable tuples, reconstructed as new `RemovedEntry` objects on reuse. Clearing before replacement bounds the memo rather than accumulating every generation seen during a sweep.

The version helper distinguishes known absence from unknown metadata failure. `_read_removed_list()` separately distinguishes an empty or missing tree list from an unsuccessful read, while the public `load_removed_list()` retains its empty-list fallback. A memo entry is admitted only when both versions are known, the tree read succeeded, and the live cache was read successfully or is known missing.

Tree metadata is checked with explicit `Path.stat()` and `S_ISREG`, not `Path.is_file()`. This preserves `PermissionError` and other `OSError` failures as unknown, including on Python versions that suppress those errors in `is_file()`. A missing or nonregular tree path yields an empty list; an unknown failure leaves the next lookup eligible to retry.

## Code

Paths refer to the mirrored core source tree.

- `hermes_cli/plugin_catalog.py`
- `hermes_cli/plugins.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The discovery-manager test substitutes an inner sweep making repeated removal lookups and checks reuse, followed by a fresh decode on forced discovery. File-backed tests cover live-cache replacement with preserved modification time, tree edits, deletion, recreation, returned-object mutation isolation, nested catalog-directory changes and exception-safe scope restoration.

Recovery tests exercise `installed_plugin_removal()` with injected live JSON and tree YAML read failures while holding the generation constant. They check that a later lookup sees a recalled plugin. Separate tests check rejection of unknown version evidence and reuse when both catalogs are absent.

Tree metadata tests inject `PermissionError` and generic `OSError` into `os.stat()` after the scoped fingerprint succeeds, then check recovery through `installed_plugin_removal()`. Unscoped controls exercise the same consumer without a memo.

Source test path:

- `tests/hermes_cli/test_plugin_discovery_reads.py`

Actual plugin execution across a full installed-plugin discovery sweep, concurrent discovery across threads, and end-to-end startup latency are untested by these patch tests. Ordinary command-level enable/reload entry paths are untested here; forced manager discovery and unscoped catalog reads are the narrower covered cases.

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

Retry eligibility is not a fail-closed recall policy: a failed read can still produce an empty contribution for the current lookup. The change prevents that failure from becoming a reusable empty verdict. It does not fetch a newer live catalog or guarantee recovery while the underlying failure persists.

Reuse is limited to one scope and still performs metadata checks. Reduced decoding is not proof of a measured whole-startup speedup, and the separate fingerprint and read operations do not form an atomic filesystem snapshot.

## Why core instead of a plugin

Removal checks gate plugin loading itself. Core owns discovery lifetime and the catalog reader, so it must distinguish successful absence from failed reads before sharing results across plugin admission decisions. A plugin cannot safely optimize the checks that decide whether it may load.
