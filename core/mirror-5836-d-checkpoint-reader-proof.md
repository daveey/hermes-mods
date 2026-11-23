# Conservative BoardWatch checkpoint retention and CPU attribution

Source patch ordinals: 5771–5777, 5817.

## Behavior

BoardWatch can retain an existing reader across a proven WAL checkpoint without rearming its repair generation. Unproven changes, restores or incompatible schema evidence reopen conservatively. Sync diagnostics distinguish wait work, reader opens/keeps and selection/checkpoint subprobes.

## How it works

Retained entries track main-file identity, WAL baseline and the schema parse baseline. Keep proof checks the same nonshrinking inode, WAL checkpoint evidence, and equality of schema cookie, `sqlite_schema` rows and main-file schema-format bytes. Reader shrink/reset exposes current file evidence rather than a cached answer. Missing lock-safe `pread`, unreadable/moving evidence or mismatches means no keep. Removing a watched Board also releases its sidecar baseline descriptor.

`WaitSplit` attributes wait-step CPU without adding another outer accounted scope. `sync.detail/<step>.<part>` records inclusive probe diagnostics, including failed proof attempts, and reader-keep attribution retains the triggering step.

## Code

- `hermes_cli/kanban_sync_board_watch.py`
- `hermes_cli/kanban_sync_wal_evidence.py`
- `hermes_cli/kanban_sync_cpu.py`
- `hermes_cli/kanban_sync_work.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/sqlite_safe_read.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_sync_checkpoint_keep.py` covers checkpoints and later commits, raw restores, replacement/shrink, WAL restart, schema-cookie/row/format changes, parsed migration restoration, unavailable headers/pread and fresh-reader equivalence. `test_kanban_sync_wait_split.py` checks attribution totals and reader opens; `test_kanban_sync_probe_split.py` covers executed-only probes, exceptions and nonadditive detail/step metrics. A fixture follow-up ages files so coarse mtime ticks cannot hide writes.

## Applying

Apply the proof and all schema/header follow-ups in order rather than extracting only the initial keep optimization. No new activation flag is required.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Same inode or same schema cookie alone is not proof. Inclusive detail metrics overlap enclosing steps and must not be summed with them or subtracted as extra outer CPU. This is a conservative cache optimization, not a general raw-restore support guarantee.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
