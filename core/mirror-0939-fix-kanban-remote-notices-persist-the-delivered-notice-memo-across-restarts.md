# fix(kanban remote notices): persist the delivered-notice memo across restarts [daveey]

Patch 0939, source `35c922ec0688de34c05026bd430240e0fc09cf03`.

## Behavior

fix(kanban remote notices): persist the delivered-notice memo across restarts [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_legacy_fresh_rows_reoffered_once_and_stale_rows_left(tmp_path, caplog):`
- `def test_sweep_skips_persisted_notices_without_opening_store_until_restart(tmp_p`
- `def test_sweep_does_not_remember_a_notice_it_could_not_write(tmp_path):`
- `def release(claim: dict, *, ledger: Optional[Path] = None) -> bool:`
- `def _remember_delivered(key: tuple) -> None:`
- `def sweep_remote_notices(*, replica_path=None, gateway=None, session_db=_session`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> 54a602e068 stopped re-writing delivered remote notices on every sweep, but kept the
> record in memory only, so the first pass after every backend restart re-opened a
> writable SessionDB and re-ran append_kanban_notice for every delivered notice (494 on
> the MacBook 2026-10-29: a burst of "30 writable SessionDB() opens in 60s" warnings for
> ~1 min after each of the ~14 core-update restarts that night).
>
> Record each persisted notice key in the remote-notices ledger (new `swept` table, next
> to the claim rows, not in the 13 GB state.db) and load it once per process. Measured on
> the live data in a fresh process: first pass 494 opens / 5.2 s wall / 4.0 s CPU; the
> next fresh process 0 opens / 0.1 s. An unwritable notice is still never recorded; an
> unusable ledger falls back to the in-memory memo; rows older than 90 days are pruned
> (a card still in the replica then costs one deduplicated append).
>
> Five whys: restart re-writes 494 notices <- the "already delivered" record lived only
> in process memory <- 54a602e068 fixed the per-pass cost and deferred durability as a
> follow-up <- the existing ledger's delivered rows mean "announced", not "persisted by
> the sweep" (stale states are recorded silently), so they could not serve as the mark
> <- root: no durable idempotency key for the sweep's own writes. Not caught: the test
> asserted "a restart re-checks once" as intended behavior and no test counted opens
> across a restart.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `tui_gateway/kanban_remote_notices.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_kanban_remote_notices.py`

New or changed test definitions present in the source diff:

- `test_sweep_skips_persisted_notices_without_opening_store_even_after_restart`
- `test_sweep_keeps_working_in_memory_when_the_ledger_is_unusable`
- `test_swept_rows_older_than_the_keep_window_are_pruned_on_load`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
