# fix(kanban): let a sweep worker archive a provably stranded runs_on copy [daveey]

Patch 0780, source `0af1a2acd89feaff179e477dc35312bb1af24d53`.

## Behavior

fix(kanban): let a sweep worker archive a provably stranded runs_on copy [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import argparse`
- `def _creator_task_id(conn, task_id: str) -> Optional[str]:`
- `def _cmd_archive(args: argparse.Namespace) -> int:`
- `def specify_triage_task(`
- `def archive_task(conn: sqlite3.Connection, task_id: str, *, signal_fn=None) -> b`
- `+"""A sweep worker may archive a foreign card that is provably stranded. [daveey]`
- `def _handle_archive(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A ready card pinned runs_on=[machine:<m>] to a machine without advertised
> pickup is never claimed. Once a sweep re-filed it on that machine, no worker
> could archive the dead copy: archive scope allowed only the creator's task.
> Allow a foreign archive when the card is ready, never claimed, every machine:
> label is refused by placement_runs_on against a reachable registry, and a
> comment says it was re-filed naming the copy. The archived event records
> via: stranded-runs_on sweep by <task id>.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_stranded_archive.py`

New or changed test definitions present in the source diff:

- `test_sweep_worker_archives_a_provably_stranded_foreign_card`
- `test_cli_archive_of_a_stranded_card_records_the_sweep`
- `test_stranded_card_with_a_claimed_event_is_refused`
- `test_running_stranded_card_is_refused`
- `test_card_for_a_machine_that_advertises_pickup_is_refused`
- `test_card_without_runs_on_is_refused`
- `test_stranded_card_without_a_refiled_comment_is_refused`
- `test_card_is_refused_when_the_machine_registry_is_unreachable`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
