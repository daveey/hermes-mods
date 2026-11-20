# Update --up-to HEAD on origin/main first-parent

Mirror snapshot 5011; ordinals 4872.

Entries:

- **4872**: fix(update): accept a first-parent HEAD target for restart catch-up

## Behavior

- `--up-to HEAD` is accepted only when that commit is on fetched `origin/main`'s first-parent line.
- Target identity is unchanged; the existing zero-count completion path is reused with no upstream sync, push or checkout move.
- `UP_TO_ACCEPTS_HEAD` is exposed for the sync runner's text capability negotiation.
- A HEAD that is a side-parent of origin/main is refused.

## How it works

- `hermes_cli/update_up_to.py` checks first-parent ancestry of the named HEAD against fetched origin/main before treating a pin-at-HEAD as already complete.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/update_up_to.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_update_up_to.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- An unpinned zero-commit update can still fetch and advance beyond a planned prebuild if `--up-to HEAD` is not used. Side-parent HEAD is not a catch-up target.

## Plugin?

None.
