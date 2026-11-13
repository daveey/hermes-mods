# CI fetch-merge-base full-history fallback

Patches: **3621**.

## Behavior
- If the filtered (`--filter=tree:0`) fetch in the fetch-merge-base action fails, the script prints a `filtered fetch failed (rc=N)` line and retries once without the filter (unshallowing if needed), instead of failing the required check.
- Partial-clone settings are restored after the fallback, so lazy fetch still works for later `git diff`; a fresh checkout ends with none of them. The merge base's tree must be readable or the step fails; a failing fallback still fails.
- A healthy filtered fetch is unchanged (same argv and output, no fallback).

## How it works
- 3621 records `remote.origin.promisor`, `remote.origin.partialclonefilter` and `extensions.partialclone` before the fetch; on failure it unsets them, fetches unfiltered, restores the recorded values, then verifies with `ls-tree`. Motivated by a git bug in promisor-mode index-pack.

## Code
- `.github/actions/fetch-merge-base/action.yml`
- `.github/actions/fetch-merge-base/fetch-merge-base.sh`
- `tests/ci/test_fetch_merge_base.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_fetch_merge_base.py`: `test_git_255_pack_objects_bug_falls_back_to_a_full_history_fetch`, `test_second_call_fallback_keeps_the_partial_clone_readable`, `test_fallback_fails_when_the_merge_base_tree_cannot_be_read`, `test_a_failing_fallback_still_fails`, `test_a_healthy_filtered_fetch_has_no_fallback`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The link between a runner git upgrade and the failure is a hypothesis, unverified in the source.
