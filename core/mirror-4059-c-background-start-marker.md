# Background process output excludes the login-shell startup banner

Mirrored entries: 3885.

## Behavior

Background process output normally begins at the command itself, not at text printed by login-shell rc files. Pipe, output-log and PTY readers use the same marker gate, including re-adopted persisted logs. If the shell never reaches the command, its held failure output is preserved instead of disappearing.

## How it works

The login argv prints a unique per-job marker on its own line immediately before exec. The gate is armed only if the actual argv includes that marker, preventing Windows or replaced argv from waiting for an impossible token. Output ingestion buffers until a complete marker line, then drops the prefix and passes subsequent command output unchanged.

`start_marker` is checkpointed; constructing a re-adopted ProcessSession re-arms the gate for log reread. EOF paths release buffered text if no marker arrived. The hold is bounded at 64 KiB: flood output is released except for a tail long enough to match a split marker. Existing shell-noise cleanup moves into process_registry_bg_shell without changing its compatibility alias.

## Code

- `tools/process_registry.py`
- `tools/process_registry_bg_shell.py`
- `tools/process_registry_output_log.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/tools/test_process_registry_start_banner.py`: Runs real fixture shell jobs in pipe/log/PTY modes and asserts banners do not reach buffers or dock activity; silent commands remain starting, rc exit output survives, persisted log adoption strips banners, replaced argv is not held, and split CRLF markers/bounded flood buffering pass the correct text.
- `tests/tools/test_process_registry.py`: Updates the shell-argv fixture seam for the optional start-marker argument, preserving registry tests rather than asserting new behavior in this edit.
- `tests/tools/process_registry_fixtures.py`: Adapts test shell construction so controlled fixtures can carry the marker; supporting infrastructure, not a separate assertion suite.
- `tests/hermes_cli/test_process_dock.py`: Adjusts its quiet-shell fixture to the marker-aware argv signature; the new end-to-end dock activity assertions live in test_process_registry_start_banner.py.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

The buffer cap intentionally releases startup floods, so absolute banner suppression is not promised for unbounded rc output. An rc file that exits before the marker exposes its diagnostic output and exit status. Watch/notify patterns scan ingested text; raw persisted output-log bytes are not rewritten. No live shell rc file or process registry was changed by this documentation pass.

## Plugin?

No. Marker creation, checkpoint adoption and all output readers need a single core gate.
