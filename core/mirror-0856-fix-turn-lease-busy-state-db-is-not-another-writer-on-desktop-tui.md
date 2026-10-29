# fix(turn lease): busy state.db is not another writer on Desktop/TUI

Patch 0856, source `7e8e8f4a599ac10d35b97c8b721db426debf6ea9`.

## Behavior

fix(turn lease): busy state.db is not another writer on Desktop/TUI. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `_REASON_LEASE_LOST = "session turn lease lost"`
- `def admit_durable_turn_lease(`
- `class SessionCompressionMixin:`
- `def test_acquire_turn_lease_retries_sqlite_lock(tmp_path, monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Desktop/TUI acquire the turn lease with wait_seconds=0, so one sqlite
> 'database is locked' during acquire hit the already-passed deadline and
> returned False, which the facade reports as 'This chat has another active
> writer' (session_busy). Seen on chat 20261025_234720_c405df right after a
> core-update backend restart: no lease row existed, and the failure
> coincided with a state.db write-lock warning.
>
> acquire_session_turn_lease gains contended_wait_seconds, a floor for the
> busy-DB retry budget only; the facade passes 15s. A real holder still
> fails fast.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `agent/turn_facade_lease.py`
- `hermes_state_compression.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_state/test_session_turn_lease.py`

New or changed test definitions present in the source diff:

- `test_zero_wait_acquire_rides_out_sqlite_lock`
- `test_zero_wait_acquire_still_fails_fast_on_a_real_holder`
- `test_acquire_turn_lease_locked_for_the_whole_wait_times_out`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
