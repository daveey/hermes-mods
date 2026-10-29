# fix(kanban): don't kill a live worker for a lease that lapsed while the Mac slept (#192)

Patch 0933, source `49dcb95c950d030182be663ee12ac3b01c1f124e`.

## Behavior

fix(kanban): don't kill a live worker for a lease that lapsed while the Mac slept (#192). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def release_stale_claims(`
- `MODES = ("off", "shadow", "primary")`
- `def stage_claim(conn: sqlite3.Connection, task_id: str, gate: Optional[ClaimGate`
- `def _renew(conn: sqlite3.Connection, task_id: str, run_uid: Optional[str], ttl_s`
- `def _attach_worker(conn, tid, proc):`
- `def test_worker_that_cannot_renew_is_stopped_before_lease_expiry(primary, lease_`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(kanban): don't kill a live worker for a lease that lapsed while the Mac slept
>
> Card t_01a18628253a543a. The Mac clamshell-slept 17:59-18:14 PDT 10-28 with live
> workers; on dark wakes the dispatcher saw their leases past expiry on its own clock
> (lease_lost) and SIGTERMed them, and the Board reclaimed at TTL. Now:
>
> - release_stale_claims asks the Board (reassert_lease) before stopping a live
>   heartbeating worker whose lease lapsed: renewed -> keep; Board unreachable and the
>   lapse < LEASE_DARK_HOLD_SECONDS (12 h, = Board DARK_HOLD_MAX) -> defer and retry;
>   refused / holder moved / offline past the hold -> stop as before.
> - _renewal_due retries a lapsed lease every 30 s instead of ttl/3: the throttle runs
>   on time.monotonic(), which stops during macOS sleep.
>
> Pairs with daveey/hermes-hub#60 (Board holds a dark holder's lapsed lease).
>
> * refactor(kanban): fold the Board reassert into lease_lost (kanban_db.py stays under its health cap)

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_sync.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_sync_leases.py`

New or changed test definitions present in the source diff:

- `test_lease_gap_does_not_kill_a_live_worker_when_the_board_still_holds_it`
- `test_lease_gap_with_the_board_unreachable_keeps_the_worker`
- `test_lease_gap_stops_the_worker_once_the_lease_is_really_gone`
- `test_first_heartbeat_after_sleep_renews_a_lapsed_lease`
- `test_live_worker_with_a_valid_lease_keeps_its_claim`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
