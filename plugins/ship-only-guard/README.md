# ship-only-guard

A public, stdlib-only command guard that routes landing through a repository's
trusted ship workflow for core main, with a narrow state-branch preflight below.
It does not merge or push. State preflight makes one bounded read-only GitHub request.

## Configuration

Create `$HERMES_HOME/ship-only-guard.json` (default home: `~/.hermes`):

```json
{"repos": ["example/protected", "example/another"]}
```

Names are exact, case-insensitive `owner/repo` matches. No file, an invalid file,
or an empty list means **no scoped guards**. No private repository names or
deployment defaults are bundled. `SHIP_ONLY_GUARD_REPOS='["example/protected"]'`
replaces the file's list, for isolated fixtures and private shim bootstrapping.
Configuration is read on each check, not cached at plugin activation.

## Behavior

The `pre_tool_call` hook checks `terminal`, `execute_code`, and nested `tool_call`
(including qualified tool names). On a configured repository it refuses:

- `gh pr merge`, regardless of merge/admin/auto flags, with URL, `-R`, `--repo`,
  `GH_REPO`, or current repository selection, except the exact state and
  upstream-sync routes below.
- `gh api repos/OWNER/REPO/pulls/N/merge` and `repos/OWNER/REPO/merges`, including
  full API URLs and leading slashes. These merge endpoints are refused regardless
  of HTTP method, so omitting an explicit write method cannot bypass the guard.
- `git push` refspecs whose destination is `main` or `refs/heads/main`, including
  `+` force prefixes, deletions, wildcard/matching refspecs, `--all`, and `--mirror`.
  Implicit pushes resolve the current branch, push remote, `remote.NAME.push`,
  `push.default`, and upstream branch configuration where available. Dry runs,
  tag-only pushes, and explicit pushes to other branches are allowed.
- The state merge route below while any review on the PR raises a non-blocking
  point with no later reply in the PR thread. **Reply before you merge; the guard
  enforces it.** A review raises a point when its body says `nit`/`nits`/`nitpick`
  or `non-blocking` without a negation directly governing it ("No nits",
  "Nits: none", "no non-blocking points" and "No blocking or non-blocking findings"
  are verdicts, "No test for X (non-blocking)" is a point). The answer is one
  non-bot PR comment containing `<!-- hermes-review-reply -->`, created after the
  review was submitted, that answers each point as "fixed in <sha>",
  "follow-up <card>", "refuted: <evidence>" or "declined: <reason>". The refusal
  names each unanswered review id and its first point line; rerun the same merge
  command after replying. A comment is a bot's when its author type is Bot,
  `is_bot` is set, its login ends in `[bot]` or is a known GraphQL bot login
  (gh shows `github-actions[bot]` as `github-actions`), or its author association is
  `NONE` (a GitHub App is never an owner, member or collaborator). Reviews or
  comments missing from the read refuse.

Repository inference follows literal `cd`, `git -C`, `env -C`, Git push URLs and
remote push URLs. Probes are bounded read-only Git subprocesses with fixed argv;
submitted shell text is **never executed** to discover cwd or remotes. Unknown or
unreadable repository identity does not invent a protected repository and does
not block unrelated repositories.

Python is inspected with the AST: literal subprocess argv, literal shell strings
with `shell=True`, `os.system`/`os.popen`, `terminal(...)`, and `tool_call(...)`.
Simple literal variable assignments are supported. Comments, printed examples,
and unrelated string constants are not treated as executed commands.

## State-targeted landing (not core main)

For a repository that also stores deployment/configuration on `state`, the owner
must first obtain **independent approval of the full current head**, verify all
required workflows on that same head (including expected workflows absent from
GitHub's rollup), and resolve review findings/bot threads. Preserve the actual
reviewer, verdict and full SHA in the owner's evidence. Genuine independent
agent review is valid caller evidence; a self-authored approval is not.

Then the supported immediate state landing command is:

```sh
gh pr merge 12 --repo example/protected --rebase --match-head-commit FULL_40_HEX_SHA
```

The parser accepts only numeric PR, explicit `--repo OWNER/REPO` (or `-R`),
`--rebase`, and `--match-head-commit` with a lowercase full SHA. No auto/admin,
implicit PR/repo, URL selector, extra flags, duplicate flags, or other merge mode.
A fixed-argv `gh pr view` (15-second timeout, no shell) must confirm the same PR
URL/number, OPEN state, base exactly `state`, matching head, and a nonempty
all-success check rollup smaller than 100 entries. The same single read fetches
`reviews,comments` for the review-reply check above and for the **exact-head
approval check**: the latest non-bot verdict on the head must approve. A verdict
is a review on that exact commit (state APPROVED/CHANGES_REQUESTED, a
`VERDICT: <x>` line, or a first line that approves; whichever of "approve" or a
blocking phrase comes first decides, so "approve (posted as COMMENT because the
account can't approve)" approves), or a PR comment that names the head (full
sha or its first 10+ hex) and carries a `VERDICT:` line. No verdict, a review
of an earlier head only, or a later request-changes refuses. A state PR with no
review at all (#2413) is refused. Missing/malformed/offline,
main/other base, changed head, pending/failed/skipped/neutral checks, a possibly
truncated rollup, unreadable reviews/comments, an unanswered review point and a
missing exact-head approval fail closed. This is not a blanket non-main exemption.

**Gate boundary:** this guard checks that an exact-head approval record exists;
it does not authenticate that its author was independent (every agent posts as
the same GitHub account), and it does not prove coverage of required-but-absent
CI. Those remain the landing owner's mandatory gates, just as for existing state
pushes. Passing this preflight alone
is NOT approval to land. Main still requires `hermes-core-wt ship`; merge API
endpoints remain blocked. Do not set a trusted marker for state landing.
The head fence is atomic at GitHub; the base read is only a preflight, not an
atomic base lock. Do not retarget a PR during landing; use server-side branch
protection against concurrent main retargeting.

To validate the route without changing the PR, run `guard.py --gh-args` with the
same arguments (see below). It only classifies; never invoke real `gh pr merge`
as a dry-run. After a genuine owner-controlled merge, read back MERGED/base/SHA
and verify postmerge CI and deployment separately.

`tests_state.py` covers allowed and refused preflights, malformed/unreadable
responses, transport failures, head/base changes, CI states, bounded rollups,
strict flags and terminal/Python/tool entry points. No test merges a live PR.

## Upstream-sync landing (main, merge commit)

An upstream-sync gate lands a fully tested sync PR into `main` as a true merge
commit, so the upstream SHA becomes an ancestor of main; the rebase-only ship path
cannot land it. The guard admits exactly:

```sh
gh pr merge 12 -R example/protected --merge --match-head-commit FULL_40_HEX_SHA \
  [--subject TEXT] [--body TEXT]
```

Numeric PR, explicit `--repo`/`-R`, `--merge`, and a lowercase full SHA are
required; `--subject`/`--body` (commit message only) are optional, each at most
once. Any other flag (`--admin`, `--auto`, `--squash`, `--rebase`, `--body-file`,
`--delete-branch`...), duplicate, URL selector or implicit PR/repo is refused
without a probe. A fixed-argv `gh pr view` (15 s, no shell) must then show the same
PR URL/number, OPEN, base exactly `main`, a same-repository head branch named exactly
`sync/upstream-<FULL_SHA>`, head SHA equal to the pin, and every rollup entry named
`All required checks pass (v2)` green (at least one present). Other checks are
deliberately not required: optional checks may be red on a sync, and branch
protection remains the real enforcement. Probe errors and malformed responses fail
closed. `tests_upstream_sync.py` covers the admitted form, refused flags, wrong
branch/base/head/repository, missing/red/pending required checks, transport failure,
terminal/Python/tool entry points and the real standalone CLI.

## Trusted land child and private gh shims

Only the trusted ship workflow's land child should inherit
`HERMES_CORE_WT_LAND=1`. The exact environment value `1` permits the command; an
arbitrary substring, quoted prose, or comment does not. A command-prefix assignment
is scoped to that command (and its child shell), never later commands after `;`,
`&&`, or a pipeline. Standalone assignment statements do not authorize later commands.

The standalone entry point has no Hermes imports or sibling-plugin dependencies:

```sh
python3 /path/to/ship-only-guard/guard.py --gh-args pr merge 12 -R example/protected
```

It exits **0** to allow, **1** to refuse (reason on stderr), **2** for bad CLI usage.
A private gh shim can invoke it with its original argv/environment before executing
real gh. Do not interpolate argv into a shell command. The public interfaces
`check_argv(argv, cwd=None, repos=None, env=None)`, `check_shell(...)`,
`check_python(...)`, and `check_tool(...)` return a refusal string or `None`.

## Scope and limitations

This is **accident prevention, NOT adversarial security**. The marker is an
unenforced convention, not proof of approval: any process can forge its own
environment. Server-side branch protection and the trusted ship workflow's checks
remain necessary. This plugin does not install or change that workflow.

This bounded analyzer is not a shell/Python interpreter. Computed argv, arbitrary
wrapper scripts, dynamic imports, HTTP clients other than `gh api`, Git aliases,
complex shell expansion/substitutions, variable-based cwd, conditional shell state,
and complete Python control-flow/scope analysis are not covered. Literal child
shells and inline Python are inspected to a bounded depth. Shell cwd tracking is
linear; use explicit tool `workdir`/subprocess `cwd` for unambiguous context. A
process lacking an explicit cwd uses `TERMINAL_CWD`, then its actual working dir.
The adapter loads the analyzer at activation; code updates need normal plugin reload.

## Tests

Run from the detached mods worktree:

```sh
scripts/test-all -k ship-only-guard
```

`tests_guard.py` covers blocked merge/API/push shapes, matching/default refspecs,
trusted-marker allow and leakage controls, unrelated repo and branch allows,
current cwd / `git -C` inference, config/override/no-config behavior, literal Python,
registered terminal/execute_code/nested tool hooks, and real standalone CLI exit
codes. Fixtures create disposable local Git repositories; no live merge/push occurs.
