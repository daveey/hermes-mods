# Kanban commit keys on coarse-timestamp filesystems, and CI test flakes

Patches:

- `patches/2185-ci-diagnose-Blacksmith-login-and-SQLite-stat-collisi.patch`
- `patches/2186-fix-ci-tolerate-coarse-SQLite-timestamps-and-isolate.patch`
- `patches/2187-fix-kanban-serialize-advisory-content-probes-across-.patch`
- `patches/2188-fix-tests-let-serve-own-its-ephemeral-ledger-test-po.patch`

Blacksmith filesystems can record two same-size SQLite commits within one mtime/ctime tick, so stat-keyed Kanban activity caches mistook a changed database for an unchanged one. A temporary diagnostic workflow measured this; the fixes harden the keys and a port-reservation test race.

## Behavior

- 2185 adds `blacksmith-diagnostic.yml` (a branch-triggered diagnostic of login-shell startup files and same-stat SQLite commits); 2186 deletes it again, so it is not part of the final tree.
- 2186: when a board file's triggers do not vouch for its content, `content_key` falls back to `("stat", stat, connection, PRAGMA data_version)`; an unreadable file yields a fresh `object()`, i.e. unknown, never proof of no change. The hub-replica signature memo is keyed by file stat, connection and `data_version`, and old replicas without `seq` invalidate on every commit.
- 2187: probes of one path are serialized through a 64-stripe `RLock` (`probe_lock`) so concurrent readers compare the same connection's `data_version` instead of turning lock contention into distinct unknown keys; the hub signature shares that lock. A busy board read is unknown, not unchanged.
- 2188: the isolated-serve ledger test passes `--port 0` and lets `serve` own the ephemeral port instead of bind/close reserving one that a competing listener could win.

## How it works

`PRAGMA data_version` is only meaningful on the same connection, so the connection identity is part of every fallback key; a reopened reader cannot reuse an older version's cache entry.

## Code

`hermes_cli/kanban_activity_revision.py`, `kanban_session_activity_hub.py`; `.github/workflows/blacksmith-diagnostic.yml` (added then removed); test files below.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/hermes_cli/test_kanban_coarse_stat.py` (DELETE and WAL journals, frozen stat), `test_kanban_content_key_concurrency.py` (8-thread barrier, board and hub), updated `test_kanban_activity_revision.py`, `test_kanban_session_activity.py`, `test_kanban_content_key_busy_budget.py`, `test_kanban_poll_cache_guarantees.py`, and `test_isolated_serve_ledger_marker.py` (a competing listener cannot steal the port). 2186 also touched `tests/tools/test_process_registry_bg_shell_bash.py` for login-rc isolation; that change was not analysed here.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

Equal-stat keys now differ across reader replacements, so some caches recompute more often than before (intended trade-off).

## Plugin

None.
