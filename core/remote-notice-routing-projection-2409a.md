# Remote notice routing projection reuse

Mirror entries: [2299](patches/2299-perf-tui-retain-remote-notice-routing-across-replica-churn-daveey.patch).

## Behavior

- Reuse per-row routing projections across unrelated replica changes and share them across chat polls.
- Fetch changed payloads only on warm reads; delete projections for removed or superseded rows and refuse reuse when sequence evidence is unknown.

## How it works

remote_index_projection keeps one file-identity/gateway generation. Warm reads collect canonical row identities and fetch changed payloads within one SQLite snapshot, using positive integer sequence equality as the reuse condition. Rows with no seq support or unknown/nonpositive seq are decoded again. Only terminal-task linked-chat routing is cached; caller-side freshness, delivery and claims remain authoritative.

## Code

- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/remote_index_projection.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/tui_gateway/test_remote_index_projection.py` — `test_only_changed_remote_rows_are_decoded`, `test_unknown_seq_does_not_hide_updates`, `test_warm_read_fetches_only_changed_payload_and_keeps_snapshot`, `test_canonical_deletion_and_legacy_done_without_notice`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

A routing-cache hit is not delivery proof or permission to reuse a claim. File replacement, gateway change and explicit reset invalidate projection state. No production performance benchmark or notice-delivery test was executed here.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
