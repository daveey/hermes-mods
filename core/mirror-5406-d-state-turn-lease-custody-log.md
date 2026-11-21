# State: log turn-lease custody events

Mirror snapshot 5406; ordinals 5375.

Entries:

- **5375**: chore(state): log turn-lease custody events so a lost lease names its producer

## Behavior

- Investigation tooling: every statement on `session_turn_leases` logs one bounded line on a dedicated logger; keys, holders and paths appear only as short hashed tags; logging failure changes no admission.

## How it works

- `hermes_state_lease_custody.py` and call sites in `hermes_state*.py`, `agent/turn_facade_lease.py`.

## Code

Shipped non-test paths (union over these entries):

- `agent/turn_facade_lease.py`
- `hermes_state.py`
- `hermes_state_compression.py`
- `hermes_state_lease_custody.py`
- `hermes_state_messages.py`
- `hermes_state_runtime.py`
- `hermes_state_worker_compression.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_state/test_turn_lease_custody_events.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Not a fix; it adds log volume.

## Why core instead of a plugin

Lease statements live in core state code.
