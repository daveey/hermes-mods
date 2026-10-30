# Land upstream syncs through a PR (protected main)

Patches: 1293 1295 1296.

## Behavior

The fork's `main` has `enforce_admins` on and requires **All required checks pass (v2)** on the exact sha; a direct push without it is refused (`GH006`), admins included. The upstream-sync gate's `land()` used to push directly and misreported the refusal as "NOT YET: main moved". Now `land()` opens (or reuses) a PR from the staged `sync/upstream-<head>` branch, reads the required check on exactly that head (pending/missing = NOT_YET; failure/cancelled = REFUSED) and merges it as a merge commit pinned to that head, with no admin override. Any GH006 / GH013 / protected-branch / base-branch-policy refusal is REFUSED with GitHub's own message (1293). 1295 keeps GitHub's `remote: error` lines first when a gate push is refused (the tail was cut off by long CI paths). 1296 adds `.github/LANDING.md` describing the PR-only landing rule (`hermes-core-wt ship` does it for local work).

## Code

`scripts/ci/upstream_sync.py`, `.github/LANDING.md`, test fakes in `tests/hermes_cli/upstream_gate_fake.py`.

## Tests

`tests/scripts/ci/test_upstream_sync.py`: `test_protected_main_lands_through_a_pr_never_a_direct_push`, `test_landing_pr_merges_only_on_a_green_required_check_at_the_exact_head`, `test_a_gh006_refusal_is_refused_with_githubs_message_not_main_moved`, `test_a_protected_staging_branch_is_refused_with_githubs_message`, `test_protection_refusals_are_recognized`; `tests/hermes_cli/test_update_fork_merge_upstream.py` and `test_update_fork_sync_identity.py` adjusted. The LANDING.md doc has no test. This mirror task did not execute them.

## Applying

Apply 1293, 1295, 1296 in order. Only meaningful on a fork with the same branch protection.

## Gotchas

Never use `--admin`; the check name is repo-specific. No plugin involved.
