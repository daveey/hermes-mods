# Kanban OOM: classify bound-agent kills and prefer command victims

Mirror snapshot 5011; ordinals 4409, 4410.

Entries:

- **4409**: fix(kanban): classify bound-agent OOMs and prefer command victims
- **4410**: justify optional PATH-only choom lookup

## Behavior

- When the kernel killed the agent's own interpreter (a separate bound process from the spawned launcher) the run was booked as a plain crash, missing the OOM-doubled cap on retry, counting toward the failure breaker and the OOM alert. The classifier now matches the kernel victim against the spawned pid and every `worker_bound` pid of the run.
- To make commands the preferred victims, terminal commands started in a Kanban run scope are wrapped with `choom -n 1000 --` when the optional tool exists; without it the plain path is used. The agent's adjustment stays unchanged.
- 4410 records the PATH-only `choom` lookup in the command-resolution allowlist (it is an optional system tool, not a managed runtime).

## How it works

`kanban_db_dispatch.py` and `kanban_worker_scope.py` for matching; `tools/terminal_oom.py` and `tools/environments/local.py` for wrapping; fixtures copy kernel-journal shapes.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_worker_scope.py`
- `tools/environments/local.py`
- `tools/terminal_oom.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/fixtures/resolution_allowlist.json`
- `tests/hermes_cli/fixtures/oom-agent-victim.txt`
- `tests/hermes_cli/test_kanban_oom_policy_continue.py`
- `tests/tools/test_terminal_oom_adj.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Evidence in the commit text came from a live A/B probe on a pool worker; this documentation pass did not reproduce it.
- On hosts without `choom` there is no victim preference.
