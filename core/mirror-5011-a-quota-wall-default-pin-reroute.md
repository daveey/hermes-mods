# Model routing: move a default-pinned chat off a quota wall when only a minority of the pool returns soon

Mirror snapshot 5011; ordinals 4450.

Entries:

- **4450**: Move a default-pinned chat off a quota wall when only a minority of the pool returns soon

## Behavior

- The wall carried only the nearest reset, so one account returning within hours kept default-pinned chats failing even though another provider in the same class had room. `SubscriptionQuotaUnavailable` now also carries `pool_resets_at` (when at least half the walled accounts can serve again), read by `may_reroute` and the deferral log check. `resets_at`, the message and every waiter are unchanged.
- Explicit pins, card pins, and walls where half the pool returns inside the 6 h hold still hold.

## How it works

`agent/model_routing_unavailable.py` and `model_routing_walls.py`.

## Code

Shipped non-test paths (union over these entries):

- `agent/model_routing_unavailable.py`
- `agent/model_routing_walls.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/agent/test_walled_default_pin.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Reroute only applies to default pins; explicit pins stay honest about the wall.
