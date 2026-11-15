# Origin-work snapshot WAL and root isolation; cheaper subagent roster

Mirror snapshot 4352; ordinals 4205, 4206, 4215, 4226.

Entries:

- **4205**: origin-work snapshot: use WAL so a save never locks out the remote-links cold read
- **4206**: subagent.roster: read the observation scope once per request, and the gateway name without scanning replicas
- **4215**: origin-work snapshot: never file one root's scope in another root's file; tests cannot write the live one
- **4226**: origin-work snapshot: a save refused for a moved kanban home no longer fails the remote-links read

## Behavior

- **WAL for the origin-work snapshot** (4205): the file was the one kanban store still in rollback-journal mode, so a save's exclusive lock refused cold readers and readers' shared locks failed writers ("database is locked"). The save path now upgrades the file through `apply_wal_with_fallback` (keeping its WAL-reset-bug gate, network/cross-VM fallbacks and never-downgrade-live invariant), bounds the `-wal` file and re-applies the OFD lock guard.
- **Root isolation** (4215): `remote_snapshot()` resolves the kanban root once, computes its scope, and refuses the save (`RuntimeError`) if the kanban home moved meanwhile, so one root's scope is never filed in another root's file. `hermes_state_guard.ensure_origin_work_test_isolation()` refuses a pytest-context process writing the live account-root file. 4226: the race raises `SnapshotRootMoved(RuntimeError)`, caught in the remote-links read (log "origin-work snapshot not saved; retrying on the next change", return the cards, retry the save on the next read); the test-isolation tripwire still raises through the read.
- **Roster poll** (4206): `subagent.roster` / `subagent.list` read the observation scope once per request and pass it down instead of once per owner; `kanban_origin.gateway_name()` returns `gateway_names()[0]` from the meta primary-key row and mail config without scanning replica rows. Every request still re-reads scope; a scope failure yields unscoped observations, not dropped rows.

## How it works

- `hermes_cli/kanban_session_activity_origin_snapshot.py` (WAL upgrade, `SnapshotRootMoved`), `kanban_session_activity.py` (`_legacy_remote_links`), `hermes_state_guard.py`, `kanban_origin.py`, `kanban_execution_observations.py`, `process_session_activity.py`, `tui_gateway/methods_subagents.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_execution_observations.py`
- `hermes_cli/kanban_origin.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_origin_snapshot.py`
- `hermes_cli/process_session_activity.py`
- `hermes_state_guard.py`
- `tui_gateway/methods_subagents.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_execution_observation_source_safety.py`
- `tests/hermes_cli/test_kanban_capacity_integration.py`
- `tests/hermes_cli/test_kanban_card_owner_profile.py`
- `tests/hermes_cli/test_kanban_execution_receipt.py`
- `tests/hermes_cli/test_kanban_gateway_name_read.py`
- `tests/hermes_cli/test_kanban_origin_snapshot_isolation.py`
- `tests/hermes_cli/test_kanban_origin_snapshot_wal.py`
- `tests/tui_gateway/test_subagent_roster_scope_once.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Existing delete-mode snapshot files are upgraded by the save path; a dead scope row left in an existing file is pruned separately, not by these entries.
- The test-isolation tripwire fails loudly by design when a test would write the live file.

## Plugin?

None.
