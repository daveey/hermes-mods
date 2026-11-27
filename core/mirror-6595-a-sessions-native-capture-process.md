# Isolated native sessions capture and bounded startup

Patches: 5958, 5959, 5960, 5978, 6012, 6013, 6014, 6034, 6035, 6118.

## Behavior

- Native sessions list/sidebar/stamps captures run in a fresh owner-custodied canonical subprocess instead of the serve interpreter, over a private framed stdin/stdout protocol (16 KiB request, 4 MiB response, length-checked before allocation) with a five-second monotonic transport deadline. Spawn and teardown are not preempted by that transport deadline, so it is not a hard total wall-clock bound.
- A fault yields a bounded CaptureUnavailable reason and an unavailable (or stale) fact, never an empty successful list. Capture rejection reasons are logged in a bounded privacy-safe form.
- Cold-session bootstrap is isolated from serve readiness: lifespan no longer awaits a fatal enriched snapshot, and the first native selector read waits at most a few seconds on owner readiness, off the event loop, with no legacy fallback and no fabricated empty result.
- Up to four views share one child bootstrap and canonical annotation snapshot (FIFO, trailing invalidations kept). The child helper path resolves through the canonical install-root API.
- Process-tree kill treats a zombie/dead target as settled so an exiting child is not waited on for the whole stop deadline.

## How it works

sessions_capture_process owns the helper lifecycle (spawn_owned custody, kill and reap on timeout or close, replacement refused after failed teardown); sessions_capture_worker is the child entry; sessions_native_source and sessions_native_startup feed the result into the ChangeFeed. A design note describing the protocol ships with the series.

## Code

Paths touched by these entries (non-test):

- `agent/deadline.py`
- `docs/designs/sessions-capture-process.md`
- `hermes_cli/sessions_capture_process.py`
- `hermes_cli/sessions_capture_worker.py`
- `hermes_cli/sessions_fact_routes.py`
- `hermes_cli/sessions_native_source.py`
- `hermes_cli/sessions_native_startup.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/agent/test_deadline_zombie_during_stop.py`
- `tests/hermes_cli/test_annotation_prepare_lifespan.py`
- `tests/hermes_cli/test_sessions_capture_batch.py`
- `tests/hermes_cli/test_sessions_capture_http.py`
- `tests/hermes_cli/test_sessions_capture_process.py`
- `tests/hermes_cli/test_sessions_cold_bootstrap.py`
- `tests/hermes_cli/test_sessions_first_read.py`
- `tests/hermes_cli/test_sessions_native_source.py`
- `tests/hermes_cli/test_sessions_native_startup.py`
- `tests/hermes_cli/test_subagent_session_activity.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Capture success is not proof of HTTP availability within any bound; listener startup and update fences are separate.
- Parent-only fixture doubles cannot be inherited by a fresh child, so tests inject a canonical inline reader explicitly.
- Evicted-capture failure episodes and malformed envelope handling are covered by dedicated tests.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes serve startup, the sessions runtime and process custody inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
