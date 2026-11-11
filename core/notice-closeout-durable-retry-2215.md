# Durable retry of a failed notice close-out wake

Patches:

- `patches/2192-daveey-Retry-a-failed-notice-close-out-wake-durably-.patch`
- `patches/2193-daveey-Preserve-due-notice-retries-beyond-the-discov.patch`

A close-out wake whose agent build failed (quota wall, refused store write) was stamped `closeout.state=error` and never offered again; the in-process automation replay's timer dies with its backend, leaving chats unresolved on `notice_tail`.

## Behavior

- A failed wake now stamps `closeout = {state: deferred, retry_at, attempts}`. The row is a candidate again once `retry_at` (the build failure's `failure_resets_at`, or the replay floor, plus 600 s) passes, at most `MAX_ATTEMPTS=3` wakes per row; the last failure stamps `error`. `refused`/`busy`/`missing` verdicts stay final. A `started` row is never woken again.
- 2193: a due deferral is exempt from the discovery lookback (`LOOKBACK_S`), so a quota reset several days out (tested at eight days) no longer silently expires the obligation. Recency, grace window, SQL eligibility and handling guards still apply.

## How it works

`notice_guarantee.candidates` accepts rows with `closeout` absent or `_deferred_due`; `_closeout_job` stamps `deferred` with `_retry_at`; the claim stamp counts `attempts`. The `automation_replay` docstring now names the deferred fallback.

## Code

`tui_gateway/notice_guarantee.py`, `tui_gateway/session_notifications.py`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/tui_gateway/test_notice_guarantee.py`: defer then retry after `retry_at`, attempt cap, only `error` defers, `_retry_at` follows the quota reset, the eight-day reset survives the lookback, grace window with a deferral.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

Rows are durable, so any backend's pass can retry them. A failure to compute the retry time stamps `error` rather than retrying.

## Plugin

None.
