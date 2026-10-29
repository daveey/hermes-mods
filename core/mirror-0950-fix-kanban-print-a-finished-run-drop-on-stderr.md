# fix(kanban): print a finished-run drop on stderr [daveey]

Patch 0950, source `48f310fd7b08f6a9f2f89a4c7260128226ab24e0`.

## Behavior

fix(kanban): print a finished-run drop on stderr [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def run_is_live(env: Mapping[str, str], task: str, run_id: int) -> Optional[bool`
- `def test_noop_when_unset(monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A one-shot hermes process only has a queue log handler, so the info
> line never reaches the operator before --version exits.

## Code

- `hermes_cli/kanban_identity.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_stale_kanban_identity.py`

New or changed test definitions present in the source diff:

- `test_drop_line_is_on_stderr_even_when_a_handler_exists`
- `test_main_drops_identity_before_plugins_load`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
