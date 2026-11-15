# Post-wake session turn-lease custody

Patches: **4335–4336** (4352 snapshot).

## Behavior

A host wake does not immediately give a live holder's expired turn lease to the first waiter. Lease-less transcript writers observe the same post-wake grace instead of deleting that holder's lease.

## How it works

A shared wake tracker compares suspend-inclusive and awake clocks. Expired leases of live holders receive a bounded awake-time grace; dead holders remain reclaimable. Runtime acquisition, compression acquisition and transcript write guards share the stale-lease rule.

## Code

- `hermes_state_compression.py`
- `hermes_state_messages.py`
- `hermes_state_runtime.py`
- `hermes_state_wake_grace.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_state/test_session_turn_lease_wake_grace.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Without a usable suspend clock the tracker does not defer ordinary expiry. A live holder that never renews is reclaimable after grace. The shipped tests drive fake clocks; this pass did not suspend a real host.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
