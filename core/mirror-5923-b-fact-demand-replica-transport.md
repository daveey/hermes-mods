# Fact demand channel and replica transport: owner epoch, replay, capacity and retry

Mirror snapshot 5923; ordinals 5879, 5882, 5883, 5886, 5894.

Entries:

- **5879**: Preserve notification board isolation and replay accepted view demands (this doc covers the demand/replica half; the notification half is in [loaded notification scheduling](mirror-5923-b-loaded-notification-fact-schedule.md))
- **5882**: Identify demand owner without exporting fact snapshots
- **5883**: Regress burst demand delivery on 85000 facts
- **5886**: Retry quiet fact replica callbacks with bounded per-subscriber backoff
- **5894**: Reserve bounded fact IPC capacity for demand channels

Builds on [shared facts core](mirror-5923-b-shared-facts-core.md). None of this is activated in production by these entries.

## Behavior

- **Demand custody (5879)**: replica consumers ask the owner to materialize a view for a selector ("demand"). The demand channel now keeps one bounded table (at most 64 live demands, 30 s lifetime, each capped at 64 KiB encoded) instead of a queue: repeated demands for the same view key refresh expiry; an accepted demand is replayed when the owner's epoch changes (owner restart/reset), but not on a same-owner reconnect. The replica reports the owner epoch it sees on pins and deltas.
- **Owner identification (5882)**: the demand connection authenticates the owner's epoch from the handshake (`hello` now carries `epoch`) instead of reading a fact snapshot to learn it, so a demand never exports facts; an owner whose hello lacks a valid epoch fails closed and the demand retries. A stale acknowledgement after a new-owner reset is replayed.
- **Burst delivery (5883)**: a regression test drives 16 burst demands against a store holding 85,000 facts and checks each is answered by materializing only the demanded key.
- **Quiet-owner retry (5886)**: previously a subscription callback that raised during drain kept its retained reset but waited for another publication that might never come. Now each pending subscription is drained independently with capped per-subscriber backoff (0.1, 0.2, 0.4, 1, 2, 5 s), healthy siblings are delivered immediately, and closing the replica cancels retries.
- **Capacity (5894)**: eight healthy replica streams used to exhaust the one handler semaphore, permanently denying demand connections. The host now has 16 handler slots with at most 8 stream slots; a handshake may request `channel: "demand"`, which is authenticated by token and scope without creating a subscription, may only issue `demand`, and releases capacity on disconnect. Legacy handshakes keep stream behavior; unknown channels are refused.

## How it works

- `hermes_cli/fact_demand.py`: `DemandChannel` holds `_active` (view key to demand with expiry and `pending`), `owner_epoch(epoch)` marks demands pending when the epoch changes, and `run` delivers pending demands over a short-lived authenticated connection.
- `hermes_cli/fact_host.py`: handshake reply includes the scope's epoch; `stream_slots` bounded semaphore of 8 within `slots` of 16; demand-channel handling.
- `hermes_cli/fact_replica.py`: forwards owner epochs to its demand channel; delivery thread tracks per-subscriber retry deadlines using `Subscription._deliver` under each subscription's delivery lock.

## Code

Shipped non-test paths:

- `hermes_cli/fact_demand.py`
- `hermes_cli/fact_host.py`
- `hermes_cli/fact_replica.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_fact_demand_transport.py` (accepted demand survives owner restart but is not replayed on a same-owner reconnect; old ACK after new-owner reset replays; active demand bound, expiry, rejection and shutdown; demand control never exports a fact snapshot; 85,000-fact burst)
- `tests/hermes_cli/test_fact_replica_retry.py` (quiet replica retries a failed callback and delivers its sibling; capped backoff stops after success; close cancels retry)
- `tests/hermes_cli/test_fact_demand_admission.py` (eight healthy replicas do not starve demands; the control channel cannot bypass scope or stream limits; admission is bounded and disconnect releases capacity)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- Owners that predate the epoch-in-hello change fail closed for demands; deploy owner and replica together.
- The 64-demand bound surfaces as a retryable full error, not eviction.
- Retry backoff is per subscriber; one permanently failing callback keeps retrying at the 5 s cap and logs each failure.
- The demand channel must never gain paging or long-poll operations; those stay on stream slots.

## Plugin

Core change; no plugin or hook is involved.
