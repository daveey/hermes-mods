# Persistent occupancy census independent of CPU policy

Mirror snapshot 5011; ordinals 4893, 4894, 4895.

Entries:

- **4893**: Drop persistent safety observation before metrics downgrade
- **4894**: Report conservative persistent occupancy independent of CPU policy
- **4895**: Kill persistent census safety mutants with isolated fixtures

## Behavior

- Persistent occupancy is observed even when CPU policy is off. Empty or unreadable pools stay UNKNOWN, never zero.
- Stamp before census; discard invalid, old, slow, or bad-count observation pairs at send without restamping. Pair rejection preserves counters without replaying safety. Older Board generations keep safety unknown.
- Occupied scope counts include active cards.

## How it works

- `hermes_cli/machine_persistent_jobs.py`, `machine_drain_metrics.py`, `machine_metrics.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/machine_drain_metrics.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/machine_persistent_jobs.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_machine_metrics.py`
- `tests/hermes_cli/test_machine_persistent_jobs.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Do not treat a missing census as proof that unscoped work is absent.

## Plugin?

None.
