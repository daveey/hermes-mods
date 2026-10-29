# fix(kanban remote notices): stop re-writing delivered notices every sweep [daveey]

Patch 0897, source `54a602e0682209255abb6b673306f8dc9a796432`.

## Behavior

fix(kanban remote notices): stop re-writing delivered notices every sweep [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_remote_completion_reaches_only_addressed_chat_and_is_idempotent(tmp_pat`
- `def test_legacy_fresh_rows_reoffered_once_and_stale_rows_left(tmp_path, caplog):`
- `def release(claim: dict, *, ledger: Optional[Path] = None) -> bool:`
- `def sweep_remote_notices(*, replica_path=None, gateway=None, session_db=_session`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> sweep_remote_notices ran append_kanban_notice for every done/blocked remote card on
> every 30 s pass, even when the notice was already in the transcript: one state.db
> writer txn plus a lineage json scan each. On the MacBook (2893 replica cards, 441
> notices, 13 GB state.db) that held the Desktop backend near one core and contended
> with live turns. Remember persisted notice ids in-process; a restart re-checks once,
> a new event has a new id, an unwritable notice is retried.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `tui_gateway/kanban_remote_notices.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_kanban_remote_notices.py`

New or changed test definitions present in the source diff:

- `test_sweep_skips_persisted_notices_without_opening_store_until_restart`
- `test_sweep_does_not_remember_a_notice_it_could_not_write`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
