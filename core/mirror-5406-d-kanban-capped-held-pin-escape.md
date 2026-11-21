# Kanban move cap: queued cards escape held machine pins

Mirror snapshot 5406; ordinals 5406.

Entries:

- **5406**: fix(kanban): let capped queued cards escape held machine pins

## Behavior

- Queued dead-pin rescue is extended only when the previous placement has exactly one `machine:` pin and its registry entry has `hold is True` (not a truthy string or integer). Running cards are not exempt. Normal registry lookup must succeed; a failed refresh does not authorize escape from a stale cached hold. Explicit caller-supplied registry mappings are also accepted by the existing move API.
- The capped rescue records `held_pin` instead of `dead_pin` and still increments move history; a later ordinary move remains capped. Target, ping-pong and source guards are retained, and the source hold is not cleared.

## How it works

- `kanban_move.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_move.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_move.py`
- `tests/hermes_cli/test_kanban_move_held_pin.py` covers held worker and hub pins, audit/count preservation, expired registry cache with successful/failed refresh, running/unpinned/multiple-pin refusals, strict boolean hold evidence, and target/ping-pong guards. The cache test uses the real registry cache with a fixture fetch callback, not a live registry service. No deployed draining-worker rescue was exercised here.

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Source hold, scaler and `max_moves` are not changed.

## Why core instead of a plugin

Move policy is core kanban.
