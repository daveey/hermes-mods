# fix(kanban): drop a finished run from startup scrub, not main [daveey]

Patch 0951, source `aaab5e3f4766a88d8532765ad9533f5f55128bdd`.

## Behavior

fix(kanban): drop a finished run from startup scrub, not main [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _board_db(env: Mapping[str, str]) -> Optional[Path]:`
- `def _default_to_chat(args) -> None:`
- `def _first_positional(argv: Sequence[str]) -> str:`
- `def test_drop_line_is_on_stderr_even_when_a_handler_exists(monkeypatch, tmp_path`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> main.py is over its line cap, so the drop runs as the first step of
> scrub_for_top_level_launch, before the command gate. A board-read
> failure is logged and does not abort startup.

## Code

- `hermes_cli/kanban_identity.py`
- `hermes_cli/main.py`
- `hermes_cli/top_level_env.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_stale_kanban_identity.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
