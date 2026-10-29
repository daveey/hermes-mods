# [daveey] fix(gateway): retry a failed startup stale-handoff reclaim instead of giving up

Patch 0913, source `589ad047e4f1f7fe0f9a865998ebf2405e4a8518`.

## Behavior

[daveey] fix(gateway): retry a failed startup stale-handoff reclaim instead of giving up. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `async def _resolve_handoff_watch_scopes(runner: object) -> list:`
- `class GatewayAdapterLifecycleMixin:`
- `+"""Handoff watcher: reclaim of handoffs stranded in running by a dead gateway, with retry.`
- `class SessionGatewayMixin:`
- `async def test_reclaim_runs_per_profile_store(monkeypatch):`
- `+"""reclaim_stale_running_handoffs must tell failure apart from "nothing to reclaim".`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> reclaim_stale_running_handoffs returned [] both when nothing was stranded and when
> the state.db write lock was unavailable, and the handoff watcher ran it exactly once
> per store at startup. A gateway started inside a lock storm (MacBook 2026-10-28 21:15 PT)
> therefore left 'running' rows in place until the next restart, blocking /handoff for
> those sessions (self-heal defect agent-9cbeb4e676).
>
> - reclaim_stale_running_handoffs returns None on failure (same WARNING), a list on success.
> - _reclaim_stale returns True on success (even []), False on None/raise.
> - The watcher keeps failed scopes and retries them at the top of later ticks with
>   backoff (30s doubling, 5 min cap), never while a dispatch of its own is in flight;
>   after 3 failures it logs ONE ERROR naming the profile store (detector signal).
>   Successful startups still reclaim exactly once per store.
> - New gateway/run_handoff_reclaim.py holds the retry state (StaleHandoffReclaimer);
>   _reclaim_stale moves there from gateway/run.py (file-size cap).
>
> Tests: watcher retry/backoff/one-ERROR/in-flight guard; real SessionDB with the write
> lock held returns None, not [].

## Code

- `gateway/run.py`
- `gateway/run_adapters.py`
- `gateway/run_handoff_reclaim.py`
- `hermes_state_gateway.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_handoff_watcher_resilience.py`
- `tests/hermes_state/test_reclaim_stale_handoffs.py`

New or changed test definitions present in the source diff:

- `test_failed_startup_reclaim_is_retried_until_it_succeeds`
- `test_reclaim_retry_respects_backoff`
- `test_reclaim_that_keeps_failing_logs_one_error`
- `test_reclaim_retry_skipped_while_a_handoff_is_inflight`
- `test_reclaim_stale_running_handoffs_flips_only_running_rows`
- `test_reclaim_returns_none_when_write_lock_is_held`
- `test_reclaim_with_nothing_stranded_returns_empty_list`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
