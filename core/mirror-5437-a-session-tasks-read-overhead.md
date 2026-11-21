# Kanban session_tasks read overhead and gateway-name memo

Mirror entries: 5407, 5408.

## Behavior

- `kanban_origin.gateway_name()` remembers this gateway's own name for 10 minutes instead of re-reading the gateway database for every card in a task list.
- The memo is keyed on the gateway database's file identity (path, device, inode) and the mail config signature, not on its WAL or mtime, which a running gateway rewrites continuously. Re-enrollment replaces the file (new inode) and busts the memo; so does a mail config change.
- After an in-place change of the stored name (never done on purpose) `gateway_name()` may lag `gateway_names()[0]` for up to 10 minutes. This is intended. `gateway_names()` (the known-gateway set) keeps its 5 second memo.
- A failed read (locked or mid-replace database), a mail config written within the timestamp granularity, and an empty name are never memoized, so a gateway that has not yet written its name is asked again on the next call.
- One `session_tasks` call (and the remote links projection) opens each read-only SQLite file once and shares that connection among all `kanban_origin._ro` callers in the call, closing it at the end, including when the call raises.
- `preserve_origin_obligations` returns the same rows as before, only faster.

## How it works

`gateway_name()` first honors a still-valid `gateway_names` memo, then a second memo (`_name_memo`) holding key, timestamp and name. A miss reads only the primary-key `meta` row and the mail config, never the replica scan.

`kanban_ro_batch` adds a context-variable batch. Inside `batch()` the first `_ro(path)` opens the file via the normal lock-guarded opener; later calls get a `_Borrowed` proxy around the same connection. Closing a borrow rolls back any open transaction and resets `row_factory` instead of closing the file, so a borrow behaves like a fresh connection. A file that is already borrowed (nested reader), or a batch inherited from another thread through a copied context, gets a private connection. Nested batches on one thread join the outer one.

Review follow-up (5408): a shared connection that was closed behind the batch's back (opening another handle can run generation recovery, which closes idle handles on a replaced WAL/SHM generation) is detected and reopened rather than lent. A batch marks itself closed on exit, so a context copied inside it and run later on the same thread neither joins nor reuses it.

In `preserve_origin_obligations` the cheap origin, released, unfinished and gateway filters now run before the five-field string check. Every filter is a plain skip, so ordering does not change the result.

## Code

- `hermes_cli/kanban_origin.py` (`gateway_name`, `_name_key`, `_ro`/`_open_ro`)
- `hermes_cli/kanban_ro_batch.py` (new: `batch`, `open_ro`, `_Borrowed`)
- `hermes_cli/kanban_session_activity.py` (batch decorators on `session_tasks` and `_project_remote_links`)
- `hermes_cli/kanban_origin_obligations.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Name-memo tests cover: kept across database writes and dropped on file replacement, mail config change seen next call, re-read after the 10 minute bound, failed read not remembered, empty name not remembered, and equivalence with `gateway_names` in each branch. Batch tests cover: one `session_tasks` call opens the shared database once and closes it, identical rows with and without sharing, borrow equals fresh connection, private connection for an already-borrowed file, nested batches, another thread never using this thread's connections, no sharing without a batch, reopening a connection closed behind the batch, the batch closing when `session_tasks` raises, and the links projection opening each file once. An equivalence test compares `preserve_origin_obligations` with the previous filter over generated ledgers.

Source test paths:

- `tests/hermes_cli/test_kanban_gateway_name_read.py`
- `tests/hermes_cli/test_kanban_session_tasks_reads.py`

The reduction in CPU or connection count on a live gateway is untested here; only the open counts in the fixtures are asserted.

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

- Anything that renames a gateway in place is invisible to `gateway_name()` for up to 10 minutes.
- A borrowed connection must be used in autocommit style; a borrower that leaves a transaction open has it rolled back on close.
- The batch is per thread: work moved to another thread opens its own connections.

## Why core instead of a plugin

The memo, the lock-guarded read-only opener and the `session_tasks` read path are internal to the Kanban core and shared by every caller of `kanban_origin._ro`. A plugin cannot intercept those calls without replacing them.
