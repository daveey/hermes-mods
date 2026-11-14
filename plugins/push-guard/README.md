# push-guard

## hermes-mods PR-only policy

`daveey/hermes-mods` PRs use `python3 plugins/push-guard/land.py --pr N --head SHA`.
Run from the deployed checkout or use its absolute path. Run under the normal authenticated
GitHub environment (for example `token-broker exec --scope user/github --reason 'reviewed PR landing' -- python3 ...`).
This path is separate from ordinary fast-forward direct pushes, which retain their existing gates.

The helper reads classic required checks plus applicable branch rulesets. Only an authoritative empty
requirement inventory permits the fallback to **all** checks and commit statuses. Every selected result
must be success on the exact head; missing, pending, cancelled, neutral, skipped, malformed and unreadable
evidence holds. Check app identity is retained; newest runs/statuses replace older results. An independent
non-bot GitHub approval must name the current full head and cannot be from the author or landing account.
Effective changes-requested reviews hold both approval routes. COMMENT prose never approves.

Where the same account implements and reviews, add `--review-task <done-card> --implementation-task <card>`.
The installed `hermes-core-wt-land` review validator must be present; its read-only `review_gate` validates
the completed run/session evidence, locally or through authenticated Board execution records. There is
no caller-supplied receipt file, approval carry or fallback to prose. Missing/incompatible validation holds.
A review card listing non-blocking points is held until a non-bot PR comment containing
`<!-- hermes-review-reply -->` is posted after the review completed ("fixed in <sha>", "declined: <reason>",
"follow-up <card>" or "refuted: <evidence>"), the same rule as `hermes-core-wt-land`'s `review_reply_guard`.
That installed guard also runs (it never posts), so points in GitHub review bodies need the same answer.

Raw `gh pr merge`, REST PUT (gh/curl/Python), GraphQL merge and auto-merge requests are refused even with
no `push-guards.json`; `PUSH_GUARD=off` does not bypass this PR-only policy. Cancel-auto and GET remain reads.
The helper performs an immediate head-pinned REST merge and exact-PR readback. Do not repeat a merge whose
write outcome is unknown: inspect the PR first. This does not change GitHub permissions. The static agent
hook is defense in depth, not an OS sandbox: opaque programs, dynamically constructed URLs and external
clients are outside its enforcement boundary. Head pinning is atomic; review dismissal/base movement
after the final read is not locked by GitHub's REST SHA parameter. Existing configured overlap guards
for other repositories are unchanged.

Tests: `tests_landing.py` covers the actual helper write boundary, independent/stale/self/bot/dismissed
review, pending/red/missing/unreadable CI, required-vs-all selection, app identity, head changes and raw
CLI/API/auto/Python/GraphQL refusal without config. Run `scripts/test-all --no-core --exact -k plugins/push-guard -k repo-guards`.

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
