# Review handoff validation, reader-cache registry, scratch archive and backlog tests

Mirror snapshot 4352; ordinals 4145, 4147, 4148, 4171, 4227.

Entries:

- **4145**: cover label backlog advice and evidence boundaries
- **4147**: fix(kanban): validate review handoffs and accept legacy automatic class routing
- **4148**: Prove replica consumer reuse and SQLite error retirement
- **4171**: fix(kanban): keep bound methods whose owner has no weakref slot registered
- **4227**: scratch archive: a kanban home kept behind a symlink no longer refuses every aged tree

## Behavior

- **Review handoffs** (4147): the override parser is shared between ingress and dispatch. Only the historical `board-default` sentinel on class selectors aliases `auto`; concrete or unknown providers with class selectors stay rejected. Invalid handoffs are refused before artifact handling or any task/run/event mutation, so a handoff that was previously persisted and then rejected at dispatch cannot happen.
- **Reader-cache registry** (4171): `_ReaderCaches.append` keeps a strong reference to a bound method whose owner has no weakref slot (and builtin bound methods) until unregistered, instead of silently dropping the registration. Builtin functions keep the weak reference; lambdas and closures stay non-owning.
- **Scratch archive** (4227): when the kanban home is reached through a symlink, each aged scratch tree was refused. `_canonical` replaces one prefix (the outermost ancestor identical by device and inode to the kanban home or the parent of a workspaces-root override) by its real location; every component is still opened without following links.
- **Test-only** (4145, 4148): label-backlog `ready_hint` and exact-label evidence boundaries; replica consumer reuse (one guarded open across an external commit) and SQLite error retirement.

## How it works

- `hermes_cli/kanban_review_model.py` (`validate_review_override`, relocated `redact_review_value`), `kanban_db.py`; `kanban_generation.py` (reader-cache registry, docstring for `register_reader_cache`); `kanban_scratch_archive.py` (`_canonical`, `_open_root`).

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_generation.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_scratch_archive.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_generation_reader_cache_registry.py`
- `tests/hermes_cli/test_kanban_label_backlog_wait.py`
- `tests/hermes_cli/test_kanban_replica_reader_consumers.py`
- `tests/hermes_cli/test_kanban_review_handoff_validation.py`
- `tests/hermes_cli/test_kanban_review_model.py`
- `tests/hermes_cli/test_kanban_scratch_gc_symlinked_home.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Lambdas and `functools.partial` with no other owner must be held and unregistered by the caller.
- Archive safety rules (links below the home, swapped workspaces) are unchanged and tested as refused.

## Plugin?

None.
