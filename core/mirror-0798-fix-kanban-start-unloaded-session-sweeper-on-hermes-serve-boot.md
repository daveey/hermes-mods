# fix(kanban): start unloaded-session sweeper on hermes serve boot [daveey]

Patch 0798, source `a24e27850bf64e8e04a562620de1bd051e4d194f`.

## Behavior

fix(kanban): start unloaded-session sweeper on hermes serve boot [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `async def _lifespan(app: FastAPI):`
- `+"""hermes serve must start the unloaded-session kanban sweeper at boot.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> An always-on hermes serve hosts chats with no /api/ws client, so the
> starters in tui_gateway entry.py and ws.py never run and unseen tui
> completions sit unclaimed. Start the same sweeper from the serve lifespan.

## Code

- `hermes_cli/web_server.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_serve_unloaded_sweeper.py`

New or changed test definitions present in the source diff:

- `test_serve_lifespan_starts_unloaded_sweeper_without_websocket`
- `test_serve_lifespan_does_not_start_sweeper_under_test_isolation`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
