# [daveey] Stop the board poll from stalling the Desktop backend

Patch 0812, source `dff3b8a924588484b9195774d0c075130478da1f`.

## Behavior

[daveey] Stop the board poll from stalling the Desktop backend. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _errors_to_500(prefix: str) -> Iterator[None]:`
- `def get_board(`
- `def _get_board_uncached(tenant, include_archived, board, workflow_template_id, c`
- `async def get_board_endpoint(`
- `def test_queue_lane_keeps_fifo_default(client):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> GET /board returned every done card with its full body. The local default
> board was 7.3MB (1859 done cards). json.dumps of that payload ran on the
> event loop and held other requests past hub-sync's 4s timeout. The hub's
> copy was 3.5MB and took 5-11s over SSH, past kanban's 6s timeout.
>
> The poll now keeps the newest 80 done cards and a 200-char body preview.
> The drawer still loads the full card from /tasks/:id. done_limit=0 returns
> the whole column. Encoding runs off the loop.
>
> The snapshot cache already kept a slow read off the poll, but still served
> and encoded the full history on every request.

## Code

- `plugins/kanban/dashboard/plugin_api.py`

## Tests

Source test files changed by this commit:

- `tests/plugins/test_kanban_board_done_order.py`

New or changed test definitions present in the source diff:

- `test_board_poll_caps_done_column_and_previews_body`
- `test_done_limit_zero_returns_every_done_card`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
