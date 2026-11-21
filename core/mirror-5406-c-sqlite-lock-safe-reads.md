# Lock-safe SQLite watcher and backup header reads

Mirror entries: 5267. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Annotation-watch signatures and backup WAL-header checks avoid plain open/close of live SQLite DB/sidecar files in a process holding SQLite connections. Such closes can cancel that process's POSIX SQLite locks. The source scanner now follows same-file wrapper functions, not just direct open calls.

## How it works

Annotation watch uses `pread_lock_safe` and backup uses `read_header_lock_safe`, reusing lock-safe descriptor handling. Missing watch files return no signature; unreadable existing files raise so callers treat them as changed. A stdlib-only standalone pre-stop process may use the explicitly marked direct-open fallback when the helper import is unavailable. The AST scanner computes parameter-opening wrappers to a fixed point, including bare functions and self/cls methods, and flags sidecar arguments at their call sites.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/backup_sqlite.py`
- `hermes_cli/kanban_annotation_watch.py`
- `scripts/check_sqlite_sidecar_opens.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests check actual process SQLite lock preservation while sampling a signature, missing/unreadable watch files, WAL-header reads without plain open, wrapper-chain/keyword/method scanner detection and nonopening-helper controls.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_backup_preflight.py`
- `tests/hermes_cli/test_kanban_annotation_watch.py`
- `tests/scripts/test_check_sqlite_sidecar_opens.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

The standalone fallback is justified only by its isolated process context, not a general exemption for gateway code. The scanner's same-file wrapper analysis is not an arbitrary intermodule alias/dataflow proof. Windows lock behavior and a full production backup/watch cycle are untested here.

## Why core instead of a plugin

File-descriptor lifetime and SQLite locking are core process-level invariants. A plugin cannot repair locks already cancelled by a core watcher read.
