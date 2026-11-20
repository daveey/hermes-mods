# Kanban sync: attachment byte priority and awake-time outbox stall aging

Mirror snapshot 5011; ordinals 4411, 4447, 4448.

Entries:

- **4411**: fix(kanban): prioritize needed attachment bytes over reverification
- **4447**: fix(kanban): age outbox stalls by observed awake operation identity
- **4448**: test(kanban): verify persisted outbox observation validation directly

## Behavior

- **Attachments (4411)**: first attempts, changed storage receipts and due keyed failures are ranked ahead of 600 s maintenance re-verification within the same limited round, so maintenance demand cannot starve new bytes. WHERE, backoff, limit and stable retry order are retained.
- **Outbox stalls (4447, 4448)**: wall-clock age counted laptop sleep as opportunity to send. Wall status stays truthful, but a stall now requires the same sendable unanswered operation for 600 monotonic seconds. Boot-scoped board observations, incident and warning state persist; they reset on identity loss, unreadable data, reboot or observation gaps over 600 awake seconds. Held FIFO/stale-wake and answered cases are preserved. 4448 validates the persisted observation directly.

## How it works

`kanban_sync_attachments.py`; new `kanban_sync_outbox_age.py` and `kanban_sync_outbox_observation.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_sync_attachments.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_outbox_observation.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_sync_attachments.py`
- `tests/hermes_cli/test_kanban_sync_outbox_awake.py`
- `tests/hermes_cli/test_kanban_sync_outbox_observation.py`
- `tests/hermes_cli/test_kanban_sync_push_starvation.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Fixtures do not establish behavior on deployed laptops.
- A long awake observation gap deliberately forgets the observation rather than inferring a stall.
