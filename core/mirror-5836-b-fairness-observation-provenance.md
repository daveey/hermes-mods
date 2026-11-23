# Persistent fairness observation IDs and writer clocks

Source patch ordinals: 5637.

## Behavior

New fairness journals preserve stable local observation IDs across exporter restarts and can optionally record wall/monotonic samples inside writer transactions. Historical missing clocks stay unknown; reports never narrow uncertainty below persisted bounds.

## How it works

Journal rows receive `journal-observation:<random-id>` identities transactionally. Clock-enabled triggers call the connection-local `hermes_fairness_clock` function registered by upgraded tracked connections, including the opening snapshot and creation receipts. Samples add sampling duration to the operator-declared clock error. Export validates persisted sample shape and emits gaps for malformed evidence. Owner JSONL has a separate 64 MiB bound and full-file validation rather than metadata-sized or tail-truncated reads.

## Code

- `hermes_cli/kanban_fairness_clocks.py`
- `hermes_cli/kanban_fairness_journal.py`
- `hermes_cli/kanban_fairness_store.py`
- `hermes_cli/kanban_fairness_metrics.py`
- `hermes_cli/kanban_fairness_schema.py`
- `hermes_cli/sqlite_safe_read.py`
- `scripts/kanban_fairness.py`
- `docs/kanban-fairness-capture.md`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_fairness_provenance.py` covers clocks/IDs surviving export and reopen, rollback, legacy gaps, malformed samples/bounds, unregistered-writer atomic refusal and clearing, owner-input bounds, explicit-database CLI guards, opening samples and uncertainty floors. Journal and vertical suites align seal/dispatch-to-report observations.

## Applying

Writer clocks are opt-in: `capture arm --db PATH --writer-clocks --clock-error-s 2` on the fairness CLI. Use an exclusively controlled capture replica with upgraded writers or establish that every writer connection has naturally upgraded. Default capture leaves clock sampling disabled. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Writer compatibility is a hard prerequisite: old/raw SQLite writers lack the function and writes fail atomically while clock triggers are armed. An upgraded exporter is not enough. If incompatible writers are found, stop capture and use `capture clear --out DIR` with an upgraded CLI, then reconnect affected writers to discard cached failed statements/schema. IDs identify local sightings, not remote Board events; never use them for cross-database lifecycle deduplication. Declared UTC error is an assumption, not an NTP measurement.

## plugin?

No. This is journal/SQLite/export infrastructure and explicit capture tooling.
