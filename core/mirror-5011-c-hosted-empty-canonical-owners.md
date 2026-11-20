# Skip hosted room discovery for empty canonical owners

Patches: **4598** (5011 snapshot).

## Behavior

When a fresh indexed canonical owner read is empty, hosted bindings return no rooms without superclass `list_rooms` discovery. Nonempty owner behavior, filtering, and order stay verbatim. Supervisor cycles and maintenance still run. Owner-read errors still propagate.

## How it works

`session_hosted_service.bindings` returns `()` only after the successful fresh owner read closes. Empty profiles do not borrow another profile's owners. Historical rooms cannot become canonical ownership via near-prefix metadata.

## Code

- `gateway/session_hosted_service.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/gateway/test_hosted_empty_owners.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Incidental superclass errors are intentionally unobserved when there are no eligible owners. Benchmark millisecond deltas are not live idle-CPU acceptance.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
