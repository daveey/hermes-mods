# Auxiliary client: no stranger-thread close of TLS sockets

Patches: **3486**.

## Behavior
- A cancelled awaited class-routed auxiliary call no longer lets a worker thread stuck in a TLS read keep a file descriptor that the loop thread already closed and the OS handed to something else (the symptom was TLS alert bytes landing in a SQLite file header).
- Replacing a cached shared client (the 401 token-refresh path) no longer closes the old entry while another caller may be mid-request on it; it is dropped like the FIFO-overflow and eviction paths.

## How it works
3486 routes the sync-SDK adapters (Anthropic, Codex, Bedrock, native Gemini) of `class_routed_async` through `aux_provider_handoff.run_in_thread`. A still-running worker is recorded as abandoned in the enclosing client scope: the loop thread only shuts the sockets down, and the worker closes both clients after its call returns. A worker cancelled before it started is not recorded. `_store_cached_client` stops closing the replaced entry.

## Code
- `agent/aux_provider_handoff.py`
- `agent/auxiliary_class_routing.py`
- `agent/auxiliary_client.py`
- `agent/gemini_native_adapter.py`
- `tests/agent/test_aux_stranger_close_siblings.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_aux_stranger_close_siblings.py`: `test_cancelled_async_class_call_never_hands_the_to_thread_workers_tls_fd_to_sqlite`, `test_replacing_a_cached_client_never_closes_it_under_a_live_tls_read`, `test_run_in_thread_returns_the_result_and_closes_on_the_caller`, `test_run_in_thread_cancelled_before_the_worker_starts_neither_runs_nor_defers`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Sibling of an earlier abandoned-provider fd-close fix; depends on its client-scope machinery.
- The replaced cache entry is dropped rather than closed, so its sockets are released by garbage collection, not immediately.
