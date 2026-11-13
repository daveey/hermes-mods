# Aux Gemini cancel-mid-stream close and socket shutdown

Patches: **3527**.

## Behavior
- Cancelling a Gemini aux stream mid-step closes the client once, on the worker thread reading it (never the event-loop thread), and the sqlite header survives fd reuse.
- An abandoned Gemini worker's sockets are now shut down, so the provider read stops after a cancel instead of running (and billing) to its read timeout.
- Bedrock is documented as not reachable by the socket sweep: a cancelled Bedrock worker keeps its fd safe but its read runs to its own timeout.

## How it works
- 3527 skips the async wrapper's close in `_close_pair` when its `_real_client` is the sync leaf being closed next (avoids a redundant close on another executor thread). `_iter_pool_sockets` also walks a client's `_http`. The `run_in_thread` docstring records the Bedrock limitation.

## Code
- `agent/agent_runtime_helpers.py`
- `agent/aux_provider_handoff.py`
- `agent/auxiliary_class_routing.py`
- `tests/agent/test_aux_stranger_close_siblings.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_aux_stranger_close_siblings.py`: `test_cancelled_gemini_stream_step_is_closed_by_the_worker_reading_it`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Bedrock sockets are not shut down on cancel (documented limitation).
