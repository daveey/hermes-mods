# Overlapped catalog fetch and automatic heartbeats

Source patch ordinals: 5633–5636.

## Behavior

Worker boot overlaps eligible Codex catalog I/O with independent initialization. Automatic Kanban heartbeat I/O runs off the turn thread with bounded single-flight scheduling; explicit lifecycle/heartbeat tools remain synchronous. Operator steering remains available even when background thread admission fails.

## How it works

Catalog prefetch starts only after final credential selection for unambiguous `openai-codex` routes without a pinned context length/custom providers. In-flight requests key by token fingerprint and endpoint; normal metadata TTL/auth/TLS/account headers/caps remain authoritative. Agent prefetch uses the ordinary context resolver so the fresh request owner persists metadata; constructor synchronization observes completion before later consumers. Heartbeats use `spawn_context_thread` to copy caller scope and retain the existing fenced writer. Delegated children schedule nothing. Start failures log a warning, clear the thread slot, consume the monotonic retry window and do not use an unfenced fallback. Steering injection is separately guarded.

## Code

- `agent/model_metadata_codex_prefetch.py`
- `agent/model_metadata.py`
- `agent/agent_init.py`
- `agent/agent_init_output_budget.py`
- `agent/activity_tracking.py`
- `tools/kanban_heartbeat_background.py`
- `tools/kanban_tools.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/agent/test_codex_catalog_prefetch.py` covers shared fresh requests, real constructor overlap, selected-route/config exclusions, token/endpoint isolation, error recovery, copied profile context, persistence and optional-start failure versus drain refusal. `tests/tools/test_kanban_heartbeat_background.py` covers nonblocking scheduling, copied run/cron scope, single-flight/rate limits, delegation, fenced descendants, start warnings, no fallback and retained steering. `tests/cron/test_cron_kanban_env_isolation.py` aligns the heartbeat seam.

## Applying

Apply the prefetch bridge, constructor use and background heartbeat refinements together. The output-budget helper extraction preserves the existing compressor reservation behavior; it is not a new tuning policy. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Overlap is not a new persistent cache and does not establish a universal latency improvement. Resource start failures may fall back to normal catalog resolution, but drain refusal must propagate. Failed heartbeat starts consume the retry window to avoid per-chunk warning storms. No live performance benchmark was performed here.

## plugin?

No. Constructor metadata resolution and turn-thread activity scheduling are core responsibilities.
