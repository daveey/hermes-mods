# Worker receipt retry boundaries

Mirror ordinals: 3952.

## Behavior

Pins sent-only WebSocket close 1011 recovery and the sticky owner-fault outcome. This patch adds tests, not a transport implementation change.

## How it works

A controlled link fails during send, before owner commit, or after commit before response. Real WorkerRPC and RuntimeSessionStore are composed with a receipt-aware fake owner. Send failures may retry at RPC level; a request already sent must retry through the receipt journal with stable sequence and payload.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/agent/test_runtime_session_store_sent_close.py`: Asserts two requests, identical parameters, retry-layer-dependent RPC IDs, exactly one committed transcript append, stored markers and an empty pending journal on recovery. Exhaustion retains sequence 1, leaves rows unmarked, maps to owner_link_closed/owner_unreachable, closes links and refuses a later append without reflush.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

A connection failure after commit is not proof that the write failed. These controlled transport tests do not establish behavior against a live remote owner.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
