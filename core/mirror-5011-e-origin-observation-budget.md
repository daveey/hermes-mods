# Bounded optional native-owner observations

Mirror entries: 4797, 4798, 4799, 4800, 4801, 4802.

## Behavior

Optional native-origin owner observations during recording cannot indefinitely monopolize a board writer. Interrupted observation transports are discarded so stale replies cannot satisfy later durable writes.

## How it works

A shared monotonic 0.5-second recording deadline covers timed lock acquisition and observation transport work. Capture warms the owner link before the transaction; recording neither reconnects a cold link nor reuses earlier authority. Nested scopes cannot extend the deadline. Outside bounded recording, ordinary locks retain their context-manager protocol. Timeout returns unavailable optional evidence. Cancellation and other exceptions detach the outcome-unknown transport before propagating, preserving exception behavior without retaining its reply queue.

## Code

- `agent/origin_managed_custody.py`
- `agent/origin_observation_budget.py`
- `agent/runtime_session_store.py`
- `hermes_cli/kanban_origin_native.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Contention fixtures exercise real owner lease queries and file SQLite writers, including success and revocation controls. Observation-budget tests cover shared/nested deadlines, deliberate per-reply delay, cold links, saturated sends and no-reply sockets, reader exit and timer boundaries. Interrupt tests cover send/receive interruption and prevent a queued observation reply from answering the next write.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/agent/test_origin_observation_budget.py`
- `tests/agent/test_origin_observation_interrupt.py`
- `tests/hermes_cli/test_origin_record_rpc_contention.py`
- `tests/hermes_cli/test_origin_worker_rpc_contention.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

This is an optional-read budget, not a durability timeout. A timed-out or interrupted observation supplies no authority. Controlled contention reproductions do not identify any historical production incident by themselves.
