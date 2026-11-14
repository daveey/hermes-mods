# Process output, compression anchors, and memo regression tests

Mirror snapshot 4059; covered ordinals: 3936, 3938, 3940, 3941.

## Behavior

These test-only patches protect copied compression anchors, split background-process start markers, interruptible idle-log waits, jobs-file memo isolation, and delta-frame memo accounting. They do not add runtime features.

## How it works

The compression tests compare source requests before/after inserting a persisted anchor. Process tests deliberately split a marker across overflow output and synchronize the finish request on entry to a long idle wait. Patch 3941 replaces a buffer-observed timing assumption with an event rendezvous and explicit teardown for a sleep mutant. Memo tests mutate returned copies, race file stamps, alternate stores, and exercise bounded/LRU frame storage rather than relying on hit counts alone.

## Code

These ordinals change only the test files listed below; the existing implementation boundaries are identified under How it works.

### Mirrored commits

- 3936: `34f7f9c4f9ab2e1ea8169c3259a39cd3106f5974` — Pins compression anchor copies without source-request mutation.
- 3938: `f54adba71ca8a4247a693bae287b662b4894db8a` — Pins split process start markers across hold-buffer overflow.
- 3940: `b7cc2dc8ac7c1517f200905df25ba969d7073ab5` — Pins idle-finish wakeups and jobs/delta-frame memo boundaries.
- 3941: `83d0353815547d138d2848b0ce80698a59a80488` — Synchronizes idle-finish regression with actual wait entry.

## Tests

- `tests/agent/test_compression_anchor_copy.py`: an inserted user anchor gets the persisted marker only on its copy; the original request/transcript is unchanged. An existing user request needs no insertion or marker.
- `tests/tools/test_process_registry_start_banner.py`: hold-buffer overflow mid-marker or at the CR/LF seam emits flood content without marker fragments; completion strips the marker and subsequent output passes through normally.
- `tests/tools/test_process_output_log_idle_backoff.py`: a finish request wakes an entered thirty-second idle wait, completes the reader, and leaves no live reader after cleanup. The follow-up rendezvous ensures the request is not set before the tested wait.
- `tests/cron/test_list_jobs_memo.py`: deep mutation of a hit cannot affect later lists; a stamp moving during parse cannot be reused even when restored later; a lock-scoped hit supplies the load stamp for save's merge-skip; alternating profile stores each retain their own memo.
- `tests/hermes_cli/test_web_delta_response_memo.py`: a nonshrinking encoded frame is remembered as whole-body fallback; oversized frames do not evict valid entries; hits update LRU order; replacing a remembered frame counts bytes once.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

A test-only mutation report is not a new execution result here. The idle test must release its teardown event even on failure. Compression persistence markers are bookkeeping on copied messages, not permission to change transcript durability in place.

## Plugin?

No. These are regression tests for existing core process, compression, and memo implementations.
