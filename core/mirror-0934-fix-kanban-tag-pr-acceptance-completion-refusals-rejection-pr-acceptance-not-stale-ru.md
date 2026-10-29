# fix(kanban): tag PR-acceptance completion refusals rejection=pr_acceptance, not stale_run [daveey]

Patch 0934, source `3b678aeda717c758bd4ace63fddb1e6997df6675`.

## Behavior

fix(kanban): tag PR-acceptance completion refusals rejection=pr_acceptance, not stale_run [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_stale_run(board, monkeypatch):`
- `from hermes_cli.kanban_capabilities import check_tool_create, declaration_requir`
- `COMPLETE_REJECTION_CLASSES = frozenset({`
- `def _handle_list(args: dict, **kw) -> str:`
- `def _handle_complete(args: dict, **kw) -> str:`
- `+"""kanban_complete DB reads: own-park detection and the refusal's PR-acceptance cause. [daveey]`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> kanban_complete raised every bare-False refusal with no unsatisfied parents as
> stale_run, including PR-acceptance refusals (no_required_checks, failing, pending
> checks), so coach digests counted PR-CI refusals as stale runs. Add the
> pr_acceptance class and stamp it when this call appended a pr_acceptance event
> with ok=false (event-id high-water mark, not prose matching). Genuinely stale,
> terminal or unknown ids stay stale_run.

## Code

- `tools/kanban_tools.py`
- `tools/kanban_tools_completion.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_complete_rejection_class.py`

New or changed test definitions present in the source diff:

- `test_pr_acceptance_refusal_is_not_stale_run`
- `test_terminal_card_after_pr_refusal_is_stale_run`
- `test_already_terminal_card_is_stale_run`
- `test_parents`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
