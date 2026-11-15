# Shutdown spool ordering and claim/replay audit tests

Mirror snapshot 4352; ordinals 4150, 4151, 4152, 4154, 4160, 4198.

Entries:

- **4150**: Test that a gate without buffer attributes streams ordinary turns
- **4151**: cron: assert lost fire-claim execution is terminal at ready-ack publication
- **4152**: test(kanban): the sync watcher fences claims from startup, before it holds .sync.lock
- **4154**: shutdown recovery: a failed compression-route read holds the chat's later spool rows
- **4160**: test a real sync-loop process publishes its held claim record before its first round
- **4198**: tests: pin #2916 resolutions — recovery egress priming stays on the loop; aux 429 recovery keeps base_url

## Behavior

- **Shutdown recovery order** (4154, production fix): a failed compression-route read in boot recovery now marks the spooled session blocked before re-raising, and the hold check matches any proven source of the route (ancestors and tip), so later spool rows for the same chat are held, in order, until the next pass; unrelated chats still progress.
- **Mutation-audit tests** (tests only): a pre-final-gate callback without buffer attributes streams ordinary turns (4150); a lost cron fire-claim's execution is terminal at ready-ack publication (4151); the sync watcher fences dispatcher claims from startup before it holds `.sync.lock` (4152); a real sync-loop process publishes its held claim record before its first round (4160); native recovery primes adapter routing on the loop thread and an auxiliary 429 recovery keeps `base_url` rows (4198).

## How it works

- `gateway/shutdown_flush.py` (`_recover_one_payload`, `blocked_sessions` and the route hold). The remaining entries add test files only.

## Code

Shipped non-test paths (union over these entries):

- `gateway/shutdown_flush.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/agent/test_session_final_buffer.py`
- `tests/cron/test_external_worker_fire_claim_adoption.py`
- `tests/gateway/test_boot_recovery_off_loop.py`
- `tests/gateway/test_kanban_sync_watcher_claim_fence.py`
- `tests/gateway/test_shutdown_route_order.py`
- `tests/hermes_cli/test_custom_endpoint_pool_origin.py`
- `tests/hermes_cli/test_kanban_sync_claim_gate.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- The tests drive real SQLite, lock files or child processes; they are timing-sensitive on loaded hosts.

## Plugin?

None.
