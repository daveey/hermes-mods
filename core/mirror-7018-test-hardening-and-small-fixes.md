# Small fixes and test-harness hardening

Patch coverage: 6606, 6607, 6635, 6636, 6638, 6651, 6656, 6663, 6666, 6720, 7016.

## Behavior

Production-side (three items; everything else in this series is tests):
- **Private app binding source (6606, 6607).** Eligibility only. A new `<private-app>_owner_response` config section defaults to `{"source": None}` (unavailable); its `source` value must hold exactly `{gateway, profile}`, each a string matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`. A missing, partial, wildcard, non-string or extra-key value, or an unavailable config backend (loaded fail-closed), makes the read-only runtime answer `unavailable` (retryable) instead of a binding. The runtime's `produce(request_id)` now also requires the courier record's `from` to equal `gw:<configured gateway>` and `base_version` to be present and explicitly null, in addition to the existing receiving-route, `tasks` collection, purpose, empty-args and key checks. A `BindingRecords` reader replaces the generic courier reader on this path and decodes strictly: duplicate JSON keys, non-finite constants/numbers, a non-object body, any top-level key other than `request`, unknown record fields and a mismatched `id` all raise `courier_record_invalid`. The configured profile is an endpoint coordinate, not sender-profile attestation (no sender profile is on the wire); no native decision or write capability is added. 6606 also ships user-guide configuration text for the setting. 6607 adds scalar/null JSON envelopes (`1`, `null`) to the strict-decode test so removing the outer object guard (`set(body)` would throw before the typed refusal) is caught.
- **MCP child adoption (6635).** `_filter_mcp_children` skips a pid whose `cmdline()` snapshot is empty (unavailable identity, like the existing `NoSuchProcess`/`AccessDenied`/`OSError` skips) instead of falling through the supervisor/marker checks and adopting it. No waits or timeouts change.
- **Canonical task pages (6663).** `_backfill_legacy_inflight_runs` (run from kanban DB connect initialization; it already skipped the sync-shadow directory) now restricts legacy run synthesis to `running` tasks with no current run **and** `sync_version IS NULL` when the `tasks` table has that column. A versioned (remotely paged) task is therefore never treated as pre-ledger local execution; unversioned legacy rows, and schemas without `sync_version`, still backfill as before. The clause applies to any such running task, not only completed ones.

Test-only:
- 6636 awaits a published exec argv (`wait_until`, 10 s) for the ordinary MCP child before the positive control and parametrizes 0 or 3 forced empty snapshots. 6638 compares renewal instants as first-mono plus injected offset rather than subtracting floats. 6651 adds a hosted-failure-evidence fixture that collects bounded read-only task/log evidence only when the deadline assertion fails (passing path does no I/O; a missing db is not created). 6656 drains every overlapping readiness poll before fixture teardown. 6666 pins an unreadable-checkpoint receipt after its turn so filesystem mtime cannot predate the completion. 6720 splits the 37-case wholesale FTS gate file into four files plus a shared helper module (assertions and timeout kept). 7016 stubs early-session, bootstrap-listener and version-prewarm startup in the serve update-wait harness, injects the sleep recorder into the real `wait_for_update_clear` call (an unrelated `sleep(0)` in the start path no longer counts) and parametrizes a one-held-probe case expecting a single 0.02 s wait.

## How it works

`configured_source()` loads the effective user config fail-closed and validates the pair. `produce()` checks the configured source and record shape before opening any board (tests assert a foreign or missing `from`, or a non-null/missing `base_version`, never touches the board db) and returns a projection only for the receiving profile's canonical board. The MCP and backfill items are single-clause guards in the named functions.

## Code

`hermes_cli/kanban_*_binding_source.py` (new) and `kanban_*_binding_runtime.py`, `hermes_cli/config_defaults.py` (new default section), `tools/mcp_tool_lifecycle.py` (`_filter_mcp_children`), `hermes_cli/kanban_db_connect.py` (`_backfill_legacy_inflight_runs`), `website/docs/user-guide/configuration.md`, plus the test files below.

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. Test source: `tests/hermes_cli/test_kanban_*_binding_source.py` and `..._binding_runtime.py` (config validation matrix, strict-JSON matrix, source/base_version refusal before board access, supported-config-reader round trip), `tests/tools/test_mcp_child_filter_supervisor.py`, `tests/hermes_cli/test_kanban_sync_shadow_backfill.py` (primary paged task before run page; legacy executor still preserved for sync and non-sync schemas), `tests/hermes_cli/test_annotation_counts_custody.py`, `tests/gateway/test_hosted_failure_evidence.py` with `tests/gateway/fixtures/hosted_failure_evidence.py` and `test_session_hosted_daemon.py`, `tests/tui_gateway/test_readiness_singleflight.py`, `tests/hermes_state/test_session_status_checkpoint_fences.py`, `tests/hermes_state/test_fts_free_gate_*.py` with `fts_free_gate_helpers.py`, `tests/hermes_cli/test_serve_update_wait.py`. Several commits are tests only. Real courier readback against a deployed peer is **untested**.

## Applying

Apply in ordinal order; most items are independent of each other but share fixtures with the neighbouring series.

## Gotchas

- Test-only commits fix harness races; do not read them as behavior changes.
- The binding source is eligibility evidence only, not a decision capability.
- The backfill guard is keyed on `sync_version`, not on completion state.

## Why core, not a plugin?

Core Kanban/runtime modules and their test fixtures.
