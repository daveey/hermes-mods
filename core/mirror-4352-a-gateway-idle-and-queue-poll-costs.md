# Cheaper session polls: update-paused turns, idle gates and delivery queue

Mirror snapshot 4352; ordinals 4237, 4238, 4239.

Entries:

- **4237**: Sessions poll: read update-paused gateway turns by index, not an admissions table scan
- **4238**: Idle heartbeat/loop gates: read state_meta by key range, not a LIKE table scan
- **4239**: Delivery-queue poll: resolve each queue path once per tick, not on every one-second look

## Behavior

Three polling paths avoid table scans and repeated path resolution with identical results.

- `turn_session_activity._update_paused_gateway_turns` (4237) now drives its join from the `state_meta` primary-key range of gateway checkpoint keys and reaches `session_admissions` by its unique `admission_id` with a `CROSS JOIN` that pins the order, instead of scanning all admissions for `status='started'`.
- `run_idle_gates._ConnMeta.list_meta_prefix` (4238) reads `key >= prefix AND key < next` plus a literal startswith filter instead of `LIKE ... ESCAPE`, so `_` and `%` stay literal and an empty prefix names no rows. `SessionDB.list_meta_prefix` is unchanged.
- `DeliveryQueueWatch._stamp` (4239) keeps each served profile's resolved queue path until the tick refreshes served homes; file and WAL stats still run on every look and a `DELIVERY_DB` override is still followed.

## How it works

- `hermes_cli/turn_session_activity.py`, `gateway/run_idle_gates.py`, `gateway/run_delivery_queue_watch.py`.

## Code

Shipped non-test paths (union over these entries):

- `gateway/run_delivery_queue_watch.py`
- `gateway/run_idle_gates.py`
- `hermes_cli/turn_session_activity.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/gateway/test_cron_delivery_housekeeping.py`
- `tests/gateway/test_run_idle_gates_empty_proofs.py`
- `tests/hermes_cli/test_turn_session_activity.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- The new tests assert the SQLite query plan is an index SEARCH; a different SQLite version could print a different plan.

## Plugin?

None.
