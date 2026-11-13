# Concurrent session writes share owner reads during gateway start

Patches: **3498**.

## Behavior
- Many session writes issued while the gateway reports `starting` (for example a bulk archive during a restart) share one owner read per home instead of each polling the control socket on its own.
- All concurrent writes still land with one mutation each; if the start persists, each caller gets the starting 503 after its own budget, and none is forwarded.
- A late caller keeps its own deadline and attempt count.
- Discovery stays read-only and never starts a gateway; no verdict older than one poll interval is reused.

## How it works
3498 routes `discover_session_owner` through `_shared_read`: the home's newest read is joined if it began no more than one poll interval before the caller asked and after the caller's own previous read; otherwise the caller starts one. An already-answered read is joined only on a re-read after `starting` was seen. Reads are shielded so a caller leaving on its deadline does not cancel others. A test also reads the Desktop request-deadline constant from its source file and asserts the starting budget plus one discovery attempt stays below it (no runtime change).

## Code
- `hermes_cli/session_owner_discovery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_session_owner_discovery_starting.py`: `test_concurrent_writes_during_starting_share_reads_and_each_sends_one_mutation`, `test_concurrent_writes_behind_a_persisting_start_each_answer_the_starting_503`, `test_a_late_caller_keeps_its_own_budget`, `test_a_shared_read_is_joined_only_within_one_poll_interval`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Callers keep their own timer loops rather than a per-home background poller; only the reads are single-flight.
- The budget guard test fails loudly if the Desktop deadline constant cannot be found in its source file.
