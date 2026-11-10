# Transient-error retries in flaky-card CI and frontend publish

Patches **2094, 2105**.

## Behavior

- 2094: the flaky-kanban CI script retries GitHub API **GET** requests up to 4 attempts with 1/2/4 s backoff on URL/timeout/connection errors and 5xx; 4xx responses and exhausted attempts raise. Non-GET requests are never retried.
- 2105: frontend `publishDirectory` removes its backup directory with a synchronous bounded retry on `ENOTEMPTY` only (the same retry helper, now parameterised by error codes). An unrelated error, or an exhausted budget, fails closed.

## How it works

`flaky_kanban._gh` loops over attempts with `time.sleep(2**attempt)`. In `frontend-common.mjs`, `retryHeld(operation, codes=heldCodes)` gained a codes argument and `removeBackup` wraps `rmSync` with `new Set(['ENOTEMPTY'])`, used at all three backup-removal sites.

## Code

- `scripts/ci/flaky_kanban.py`; `scripts/build/frontend-common.mjs`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/ci/test_flaky_kanban_github_retry.py`: GET recovers after a transient error; exhaustion raises the last error; client errors are not retried; non-GET is never retried. `tests-js/frontend-common-publish.test.mjs`: backup cleanup retries ENOTEMPTY after publishing; direct publication finishes cleanup synchronously. Whether the retry budget suffices on a given filesystem is untested.

## Applying

Apply ordinal(s) 2094, 2105 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related: [kanban-overlap-url-slug-and-flaky-cards.md](kanban-overlap-url-slug-and-flaky-cards.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

Retrying POSTs after an ambiguous failure could duplicate side effects, hence the GET-only rule. The JS retry blocks the thread by design (`Atomics.wait`) to keep publication synchronous.

## Could this be a plugin?

No. They are repository CI and build scripts.
