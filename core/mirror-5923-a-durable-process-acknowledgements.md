# Durable process-exit acknowledgements

Patch coverage: **5841–5842**.

## Behavior

Acknowledging a retained process exit remains visible to a fresh registry or another interpreter, so a handled failure does not reappear solely because status readers restarted. An ordinary unacknowledged poll does not clear failure.

## How it works

Completion consumption atomically writes a separate private `.ack` receipt next to retained process results. It records the exact process id, start time and parent session. Strict status reads compare the complete tuple and expose `acknowledged`; session snapshots suppress a retained failed exit only when that acknowledgement or the existing in-memory consumed state applies. Result rewrites cannot erase the sidecar. Expired result cleanup removes acknowledgements, and orphan sidecars are eventually pruned. Sandbox processes do not write host acknowledgements.

## Code

- `tools/process_registry.py` and `tools/process_registry_obligations.py`: completion consumption entry paths.
- `tools/process_registry_results.py`: `record_consumed`, `completion_consumed`, `status_exit_fields` and pruning.
- `hermes_state_session_status_snapshot.py`: failed-exit handling.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

`tests/hermes_state/test_process_status_durable_ack.py` covers acknowledged `poll`, `wait` and `read_log`, fresh registries and a separate interpreter, plain poll, process-id reuse, changed owner, late result rewrite, unreadable receipts, failed writes, sandbox exclusion and expiry. Patch 5842 pins fixtures to the current receipt generation so a later successful turn cannot make these assertions vacuous.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Unreadable acknowledgement evidence leaves coverage unknown and retains prior failure. Failed disk writes log a warning and must not fabricate durable handling. An acknowledgement is not interchangeable between process incarnations or owners.

## plugin?

No: completion obligations, retained results and session-status aggregation must share core lifecycle semantics.
