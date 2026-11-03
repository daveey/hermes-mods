# push-guard

## Landing-overlap gate (`gh pr merge` on a `require_check` repo)

After the exact-head check passes, the guard asks GitHub's compare API how far the base branch moved since
`merge-base(head, base)`. If any of those commits change a file the PR changes, the merge is refused: the green
check ran on an older base, so the tree that would land was never tested. The message names the overlapping
files, the commits that changed them, and the landing path (`hermes-core-wt ship <slug>` for `wt/*` branches,
otherwise rebase, push, wait for the check on the new head). A moved base with no shared files is admitted.
Compare lists only 300 files; when that list is full and shows no overlap, each PR path's base-branch history is
read instead. Unreadable compares, 1000+ commits of drift, or a truncated compare plus 60+ PR files block as
unverified. `PUSH_GUARD=off` still skips it. Tests: `test_landing_overlap.py` replays PR #475 (refused) and
PR #681 (admitted) from recorded API answers in `fixtures/`, through terminal and execute_code.

## API merges (`gh api`, curl, Python HTTP, GraphQL, `--auto`)

`gh pr merge` is not the only way to land a PR. On a `require_check` repo the guard also gates:

| Entry | Treatment | Tests (`test_api_merge.py`) |
|---|---|---|
| `gh api -X PUT repos/<o>/<r>/pulls/<n>/merge` (also `-XPUT`, `--method PUT`, `--method=PUT`, `{owner}/{repo}`), terminal | exact-head check on the `sha` field (else the PR head), then landing overlap, same messages as `gh pr merge` | `test_rest_merge_pr475_terminal_*`, `test_every_put_spelling_is_a_merge`, `test_rest_merge_checks_the_sha_field_else_the_pr_head`, `test_rest_merge_owner_repo_placeholder_*`, `test_rest_merge_wrapped_in_token_broker_is_seen` |
| the same through execute_code (shell string or subprocess argv list) | same | `test_rest_merge_pr475_execute_code_*[string\|argv]`, `test_rest_merge_pr681_is_admitted[execute_code]` |
| `curl -X PUT …/pulls/<n>/merge` (terminal) | same | `test_curl_put_to_the_merge_endpoint_is_gated` |
| Python HTTP PUT to `…/pulls/<n>/merge` (urllib etc., execute_code) | same | `test_python_http_put_to_the_merge_endpoint_is_gated` |
| GraphQL `mergePullRequest` / `enablePullRequestAutoMerge` (`gh api graphql`, argv, `…/graphql` URL), terminal and execute_code | refused on a guarded repo, or when its PR node id cannot be resolved; points at `gh pr merge --match-head-commit` | `test_graphql_merge_*`, `test_naming_the_mutation_without_calling_graphql_is_not_a_merge` |
| `gh pr merge --auto` onto the guarded branch | refused: the gates run when the command is issued, GitHub merges later | `test_auto_merge_on_a_require_check_repo_is_refused`, `test_auto_merge_on_a_merge_train_repo_is_left_alone` |

A `GET` of the merge endpoint (the "is it merged" read) is not a merge. `PUSH_GUARD=off` skips all of these. Why:
agents landed daveey/hermes PRs with `gh api -X PUT …/pulls/158/merge -f sha=…` on 2026-10-28, a path the guard never
saw; and 7 `gh pr merge --auto` calls hit daveey/hermes in the 14 days to 2026-11-03, one of which (PR #180) merged
before CI finished.

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `push-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.
