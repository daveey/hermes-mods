# test(delegate): survive a throttled runner when waiting for a background completion [daveey]

Patch 0956, source `7b909338a4a60739db88ac99bd5eac0c420d6d4b`.

## Behavior

test(delegate): survive a throttled runner when waiting for a background completion [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_nonfinite_marker_preserves_background_dispatch(harness, monkeypatch, ma`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> queue.Queue.get times out on wall clock. A 5s budget expired on CI after the
> children had already printed complete, because post-child persist had not yet
> published the event. 60s still returns as soon as the event is queued.
>
> Problem: test_nonfinite_marker_preserves_background_dispatch[False-None] failed
> with _queue.Empty on the first attempt of run 40846416856 and passed on retry.
> Why 1: completion_queue.get(timeout=5) raised Empty (queue.py:209).
> Why 2: the event is put only after post-child persist (async_delegation.py),
> which had not finished inside 5s of wall clock.
> Why 3: Queue.get's deadline is time.time(), so a CPU stall advances it while
> the producer is not scheduled. A 6s SIGSTOP during get reproduces Empty; the
> same stop passes at 60s.
> Root: the assertion's deadline was shorter than the work it waits on under
> the runner's CPU quota.
> Not caught: the file retry paints pass-on-retry green, so CI stayed green.

## Code

- `tests/tools/test_finite_delegation_outcomes.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_finite_delegation_outcomes.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
