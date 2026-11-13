# Relay managed stream: no stranger-thread close of a provider stream

Patches: **3547**.

## Behavior
- Closing a managed LLM stream (a stop, a stream retry, a consumer stopping early) while a chunk read is in flight no longer closes the provider's TLS socket from a different thread than the reader.
- `close()` on the managed stream returns only after abandoned reads have unwound (bounded wait of 2 seconds).
- A stream that finished normally still closes its provider stream as before.

## How it works
3547 runs each provider chunk read through `aux_provider_handoff.run_in_thread` inside a new `collect_abandoned()` block, so a read cancelled mid-flight is recorded. If a read was abandoned, the provider generator's `finally` only shuts the socket down (through the stream's `.response`, safe from any thread) and defers `raw_stream.close()` to the reading worker via `release_client` with a new `shutdown=` hook. With no abandoned read the stream is closed in place. `ManagedLlmStream._close` waits up to 2 s for abandoned reads, `ProviderAttempt` gains `wait()`, and `client_scope` now uses `collect_abandoned`.

Root cause: the relay prefetches chunk N+1 before yielding chunk N, so a reader is usually parked on the socket. Cancelling an `asyncio.to_thread` read cannot interrupt it, so closing the socket let a recycled file descriptor number receive the reader's bytes.

## Code
- `agent/aux_provider_handoff.py`
- `agent/relay_llm.py`
- `tests/agent/test_relay_stream_stranger_close.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_relay_stream_stranger_close.py`: `test_closing_a_live_managed_stream_never_hands_the_readers_tls_fd_to_sqlite`, `test_a_finished_managed_stream_still_closes_its_provider_stream`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The 2 s wait is bounded; a read that does not unwind in that time is not waited for further (untested).
