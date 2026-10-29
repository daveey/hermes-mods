# fix(kanban): audited orchestrator re-point of a completion contract that can never pass [daveey]

Patch 0905, source `2f4774720dffc10774a4f2e9ff2294dfcc9f1230`.

## Behavior

fix(kanban): audited orchestrator re-point of a completion contract that can never pass [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_complete(args: argparse.Namespace) -> int:`
- `def _cmd_edit(args: argparse.Namespace) -> int:`
- `def edit_operator_fields(`
- `_SPECS = [`
- `from urllib.parse import quote`
- `def _api(endpoint: str, *, query: str | None = None, paginate: bool = False,`
- `def collect_acceptance(contract: str, published_pr: str | None,`
- `def test_reopen_requires_reason(kanban_home, capsys):`
- `def test_wrapper_interpolation_uses_assignee_scope(tmp_path, monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A finished card whose completion gate can never pass had no way out except a
> human "approve closing this card" ask. Seen 2026-10-28: a card bound to a PR on a
> suspended GitHub account (the merge already lived on the repo's new home) blocked
> on David, and a card bound to OWNER/REPO for a repo with no required checks would
> have hit the same wall at the end of a multi-hour run.
>
> - `hermes kanban edit <id> --completion-contract <local-only|OWNER/REPO|PR URL>
>   --reason "..."` (kanban_db_operator.set_completion_contract): validated like
>   kanban_create, refused on done/archived cards, with an empty reason, mixed with
>   other edit flags, or inside a kanban worker (HERMES_KANBAN_TASK), like unblock.
>   Records a contract_changed event (from, to, reason, actor) and a CONTRACT
>   CHANGED comment, and notifies the board sync.
> - Gate: gh exits 1 on GraphQL NOT_FOUND before the `repository is None` check, so a
>   suspended/moved/invisible repo was classified infra ("retry completion") forever.
>   It is now auth, as that check intended. The auth and no-required-checks details
>   name the orchestrator re-point and tell a worker to block without a human_request.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_operator.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_pr_acceptance.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_edit_operator_fields.py`
- `tests/hermes_cli/test_kanban_pr_transport.py`

New or changed test definitions present in the source diff:

- `test_contract_repoint_lets_a_card_bound_to_a_vanished_repo_complete`
- `test_contract_repoint_records_event_and_comment`
- `test_contract_repoint_works_on_a_blocked_card`
- `test_contract_repoint_same_value_appends_nothing`
- `test_contract_repoint_refusals_change_nothing`
- `test_contract_repoint_is_refused_inside_a_kanban_worker`
- `test_contract_repoint_refuses_closed_cards`
- `test_contract_repoint_refuses_unknown_task`
- `test_graphql_repo_not_found_is_auth_with_the_repoint_path`
- `test_no_required_checks_names_the_repoint_path`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
