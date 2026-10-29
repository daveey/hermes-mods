# fix(kanban): self-heal fix card may requeue the card it repaired [daveey]

Patch 0937, source `3eaf0d9d4a05020805fbcaa63fc388046f5ff140`.

## Behavior

fix(kanban): self-heal fix card may requeue the card it repaired [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _is_stranded_runs_on_copy(conn, task) -> bool:`
- `+"""A self-heal fix card may requeue the one card named in its defect.`
- `def _check_kanban_mode() -> bool:`
- `def _maybe_subscribe_on_touch(conn: Any, task_id: str) -> bool:`
- `def _handle_origin_adopt(args: dict, **kw) -> str:`
- `_TOOLS = (`
- `KANBAN_UNBLOCK_SCHEMA = _schema(`
- `+"""Self-heal requeue: the one unblock a worker may perform on another card.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A worker that fixed a deterministic crash could not return that card to
> ready: unblock is orchestrator-only, the shell is fenced, and assignment
> routing has no supervisor and is told not to unblock. A self-heal fix card
> (created_by=self-heal, key selfheal:<fp>[:rN]:fix) may now unblock the
> single card named in its defect. A card that finished fix card created may
> do the same, so a completed repair is not stranded. Logged as
> self_heal_requeue. The shell fence is unchanged.

## Code

- `hermes_cli/kanban.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_tools_self_heal.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_self_heal_requeue.py`

New or changed test definitions present in the source diff:

- `test_self_heal_fix_worker_requeues_named_triage_card`
- `test_self_heal_fix_worker_cannot_requeue_a_different_card`
- `test_ordinary_worker_still_cannot_unblock`
- `test_investigation_card_and_plural_defect_are_not_a_grant`
- `test_child_of_done_fix_card_can_requeue_the_same_card`
- `test_child_of_unfinished_fix_card_cannot_requeue`
- `test_later_generation_fix_key_is_a_grant`
- `test_self_heal_worker_cli_unblock_stays_fenced`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
