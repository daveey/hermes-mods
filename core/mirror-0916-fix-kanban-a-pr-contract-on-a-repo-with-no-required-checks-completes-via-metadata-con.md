# fix(kanban): a PR contract on a repo with no required checks completes via metadata.contract_waiver, not a hand-edited kanban.db [daveey]

Patch 0916, source `f441e3ff10840c59ed05a1d5fc682c861f47c0a8`.

## Behavior

fix(kanban): a PR contract on a repo with no required checks completes via metadata.contract_waiver, not a hand-edited kanban.db [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from hermes_cli import kanban_db_notify as kbn`
- `def _cmd_create(args: argparse.Namespace) -> int:`
- `def _cmd_complete(args: argparse.Namespace) -> int:`
- `+"""Completion-contract CLI/tool helpers: the audited orchestrator re-point`
- `def observe_owner_task(conn: sqlite3.Connection, task: dict, *, now: float) -> d`
- `def validate_contract(value: str | None) -> str:`
- `def collect_acceptance(contract: str, published_pr: str | None,`
- `def prepare_acceptance(conn, task_id, expected_run_id, metadata):`
- `def record_acceptance(conn, task_id, acceptance):`
- `def github(tmp_path, monkeypatch):`
- `def test_assigned_card_with_unresolvable_profile_is_auth_not_ambient(tmp_path, m`
- `def test_no_required_checks_names_the_repoint_path(monkeypatch):`
- `from hermes_cli.goals import handoff_judge_goal, judge_goal`
- `def _handle_create(args: dict, **kw) -> str:`
- `KANBAN_CREATE_SCHEMA = _schema(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Acceptance classified 'no required checks' as missing; the only way out was an
> orchestrator re-point (2f4774720d), so a worker finishing such a card still had to
> block, and one rewrote completion_contract in kanban.db instead (agent-650ad5206a).
>
> - new classification no_required_checks; its detail names metadata.contract_waiver
>   and the orchestrator re-point (test_kanban_pr_transport expectation updated to
>   the new classification).
> - complete_task honours contract_waiver={reason} only when the fresh read is
>   no_required_checks; logs contract_waived (+ the pr_acceptance receipt), and the
>   origin ledger accepts that receipt. Every other classification stays refused.
> - kanban_create tool and CLI warn (never refuse) when the contract repo's default
>   branch has no required checks.
> - CLI complete names the PR acceptance reason instead of 'unknown id or terminal state'.
> - contract CLI helpers (incl. the moved _cmd_edit_contract) live in
>   hermes_cli/kanban_contract_cli.py (file caps).

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_contract_cli.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_pr_acceptance.py`
- `hermes_cli/kanban_pr_acceptance_store.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_pr_transport.py`

New or changed test definitions present in the source diff:

- `test_no_required_checks_names_the_waiver_and_waiver_completes`
- `test_waiver_never_bypasses_configured_required_checks`
- `test_waiver_via_kanban_complete_tool`
- `test_waiver_via_cli_complete`
- `test_create_warns_when_contract_repo_has_no_required_checks`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
