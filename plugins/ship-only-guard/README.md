# ship-only-guard

A public, stdlib-only command guard that routes landing through a repository's
trusted ship workflow for core main, with a narrow state-branch preflight below.
It does not merge or push. State preflight makes two bounded read-only GitHub
requests (the PR view and one base compare), plus at most three bounded
per-file commit lookups when it refuses for base drift. Turn eligibility adds the
bounded snapshot and server-push activity reads described below.

## Preflight budgets

The registered `pre_tool_call` callback retains a **30-second** host backstop.
The adapter and standalone `--gh-args` entry point share an **18-second aggregate
monotonic deadline** across probes, including nested parallel workers. A deadline
refusal names the stage (approval/carry, tree/blob reads, Git fetch/merge-tree,
land-turn reads, or drift); late results never allow a command. Every worker gets
its own copied context with the same deadline, and is joined before return.

Independent carry patch reads, blob reads, and the two clean-merge checks run in
parallel. Both the API tree/content prefilter and authoritative Git merge-tree
must still pass. Approval and a **read-only** land-turn scan overlap; that scan
never deletes refs speculatively. Non-speculative turn readers retain cleanup.
The mutable base-drift read stays **last**, after approval/hold checks, so the
optimization does not widen the final base-read race. Optional turn acquisition
starts only when its budget fits; lack of time never changes a refusal to allow.

Preflights taking at least one second log elapsed time and allowed/refused status,
without logging submitted commands. `tests_budget.py` and `tests_parallel.py`
cover registration, clipping, nested context propagation, named real subprocess
expiry, late-result rejection, exception propagation and host fail-closed dispatch.
Private incident recordings stay out of this public repository.

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
sha or its first 10+ hex) and carries a `VERDICT:` line, or whose first line is a
review of that head (names it, says "review") and approves; a reply first line
("Re review …", "Addressed …", "Accepted …", "Landing …") does not count. Blocking
phrases include request changes, cannot/unable to/would approve, no approval, needs
changes and withheld. No verdict, a review
of an earlier head only, or a later request-changes refuses. A state PR with no
review at all (#2413) is refused.

**Patch-identical update-branch carry.** When the head has no verdict at all,
the newest exact-head approval of an earlier head H0 (a review on that commit,
or a comment naming its full sha the same way; the newest 2 distinct approved
shas are tried) counts for the head H when all hold, read through gh only:
(a) `gh api repos/OWNER/REPO/compare/H0...H` is `ahead` with `behind_by` 0, and
every commit on H's first-parent line back to H0 (at most 3; parents from that
compare, else `gh api repos/OWNER/REPO/git/commits/<sha>`) is a merge whose
second parent is on `state` (`compare/<parent>...state` ahead or identical);
(b) `git patch-id --verbatim` of the PR diffs `compare/state...H0` and
`compare/state...H` (diff media type, each merge-base..sha) is the same single
id (whitespace-sensitive: under `--stable` a merge that only re-indented a PR
line or put a space inside a string literal kept its id and carried); (c) no
non-bot blocking verdict on any head (or a verdict with no readable
time) came at or after that approval; (d) each of those merges is a clean,
unedited auto-merge: its tree (`gh api repos/OWNER/REPO/git/trees/<sha>?recursive=1`)
is exactly the three-way merge of its first and second parent over their merge
base (`compare/<first>...<second>`'s `merge_base_commit`). A path only one
parent changed takes that parent's entry; a path both changed differently is
merged locally by `git merge-file` on the three blobs (`git/blobs/<sha>`,
checked against their ids; at most 8 such paths, 1 MiB each). Any conflict,
add/add, modify/delete, symlink or submodule on both sides, truncated tree or
other odd read refuses. This path-wise check is only a rejection prefilter:
rename/delete conflicts can look clean path by path. Every survivor must also
pass real `git merge-tree --write-tree <first> <second>` with exit 0 and a
single tree id equal to the fetched merge's tree. A scratch bare repository
under `$HERMES_HOME/cache` fetches those three SHAs with `--depth=128`, no tags
or submodules, and 30-second command timeouts. Full blobs avoid lazy fetches
inside merge-tree; authentication uses `gh auth git-credential` and inherited
Git configuration is disabled. Multiple merge bases, shallow cuts before the
base, failed fetches, unsupported Git versions and odd output all refuse;
the scratch repository is removed on exit. No carry is inferred from the
path-wise approximation alone.
Any failed or odd read refuses with the
reason; a blocking verdict on H itself is never overridden. Base drift is
checked separately and still applies. Why: #1812, #1837 and #2438 were refused
although `gh pr update-branch` left their patch-id unchanged. Missing/malformed/offline,
main/other base, changed head, pending/failed/skipped/neutral checks, a possibly
truncated rollup, unreadable reviews/comments, an unanswered review point and a
missing exact-head approval fail closed. This is not a blanket non-main exemption.

**Why a carry needs no confirm review on the new head.** A carry does not ask
for an `Exact-head confirmation` review on H, by design (decided in
t_01a1d6c53d57c331, option B). A confirm review of a clean update-branch head
only re-checks that H is the approved patch on a newer base. The guard computes
that mechanically from GitHub's own data:

- (a) shows the head only merged `state` in. H0 is an ancestor of H. Every
  first-parent commit after H0 is a merge whose second parent is on `state`.
- (b) shows the PR's own diff against its base is the reviewed one, byte for
  byte and whitespace included. An edit hidden in a merge commit (an "evil
  merge") changes that diff, and so does a conflict resolution, which alters
  the `-` lines or the context. Either one refuses.
- (d) shows each merge is exactly the clean auto-merge of its parents, so
  nothing the patch-id cannot see slipped in either: patch-id hashes hunks
  without their line numbers, and a merge that moved an identical hunk,
  context included, to another place in the same file kept the id. Its tree
  differs from the clean merge, so it refuses.
- (c) means no later objection is overridden.

What a reviewer could add beyond identity is how the PR behaves on the new
base. The guard already requires an all-success check rollup on the exact head
H, and the base-drift check below covers state moving after that; confirm
reviewers were never asked to redo that judgment. The guard cannot verify who
wrote a review either, because every agent posts as the same account (gate
boundary below). A confirm comment therefore adds only a hand-copied version of
(b).

Measured on the state repo, 250 merged PRs from 2026-11-12 07:47Z to
2026-11-13 21:07Z: 35 (14%) landed on an update-branch merge head, and 29 of
those heads have a review or verdict comment on them. None of the 29 found a
changed patch. The only one with findings (#1210) was a full post-merge review
that flagged untested behaviour, not identity. Pre-merge confirms arrived a
median of about 10 minutes after the update, time in which `state` can move
again. Before the carry existed, #1812, #1837 and #2438 were refused for lack of
one.

**Base drift under CI.** A `pull_request` run tests a merge ref built on some
`state` commit, not the `state` tip at merge time. The PR's recorded
`baseRefOid` is at or before the base CI tested: live #2507's checkout was
`Merge 3ac709e8e8 into f5f7030b43` while its `baseRefOid` c67ac3f18d sat 15
commits behind, and #2491 shows GitHub does not refresh it on a later
mergeability recompute. Comparing from `baseRefOid` therefore covers a superset
of the untested window (it can over-refuse, not miss). A retarget without a CI
rerun could make `baseRefOid` newer than the tested base (fail open), so never
retarget during landing. After the checks
above pass, the guard reads `gh api repos/OWNER/REPO/compare/<baseRefOid>...state`
(fixed argv, 15-second timeout) and refuses when a file changed on `state` since
that base overlaps a PR file:

- the same directory cut to its top three segments (`hosts/<host>/bb-tuner/`,
  `home/scripts/`), except distinct direct children of `home/bin/` or
  `hosts/<host>/bin/`; these flat executable directories still overlap for the
  same file or test/code coupling below. Nested directories retain area checks;
  the exemption is name-based and does not infer script/helper dependencies;
- the same file for a repo-root file or one directly in a top-level dir
  (`tests/test_x.py`, `docs/x.md`, whose dozens of unrelated modules would
  otherwise hold every PR);
- a shared support file directly in a top-level dir: a name not starting with
  `test_` and not markdown (`tests/sync_test_support.py`, `tests/__init__.py`)
  on either side overlaps any file directly in that dir on the other side;
- test and code: a `test_<stem>[_suffix].py` module anywhere on one side and a
  code file (`.py`, `.sh`, `.js`/`.mjs`/`.cjs`/`.ts`, `.swift` or no extension)
  on the other whose basename stem, with `-` read as `_`, equals the key or
  extends it by `_<suffix>` either way (`tests/test_hub_read_examples.py` ~
  `home/scripts/hub-read.py`, `tests/test_foo.py` ~ `home/bin/foo-cli`). Stems
  under 4 characters and generic ones (`__init__`, `main`, `utils`, `config`...)
  never match;
- any `.github/workflows/` file, or a CI harness file at the root or directly
  in a top-level dir (`requirements*`, `run_ci.py`: `tests/run_ci.py`,
  `tests/requirements-ci.txt`) on the base side, which holds every PR.

Renames count both paths. Churn elsewhere (`sync:`
commits under other `hosts/<host>/`, `home/skills/…`) does not refuse. Nested
areas (`home/scripts/` vs `home/scripts/tests/`) are not a prefix match: the
test-and-code rule covers the module pairs that matter there, and a prefix rule
measured on live PRs held sync churn (see the commit message). The
refusal names the overlapping files (with the rule and the PR file for the two
cross-file rules), the newest drift-window commit that touched each of the first
3 files (one bounded `gh api repos/OWNER/REPO/commits?sha=state&path=<file>&per_page=5`
each, 8-second timeout; a failed lookup only drops that attribution), and the
base commits (content commits first, the first 8) and asks for
`gh api -X PUT repos/OWNER/REPO/pulls/N/update-branch -f expected_head_sha=<head>`,
green CI on the new head and a rerun; the exact-head approval check then applies
to the new head (an approval of the pre-update head carries when the patch-id is
unchanged, above). A missing/malformed `baseRefOid`, a missing, empty or 100-entry
(possibly capped) `files` list, a failed or unparsable compare, missing
`total_commits`/`commits`/`files`, 250+ commits, 300+ files, or a base that is
not an ancestor of `state` fail closed. Why: #2491's CI tested base 3a55357793;
#2487 (adc4b89b37) then changed `bb_tuner_feed.py` in the same directory, #2491
was rebase-merged without a check on the combination and state went red.

Skill prose is exempt: a skill markdown file (`home/skills/**.md`, `skills/**.md`)
on the base side never overlaps a skill markdown file in the PR, the same file
included. The suites lint those files one at a time, and a textual collision still
fails GitHub's rebase merge. hermes-sync pushes live skill edits straight to
`state`, and its `sync: <host>` commit 5fbd15ce4f was one of #2563's refusals.
Skill prose next to code (`home/skills/x/scripts/*.py`) and markdown outside
the skill trees still overlap.

**Ordered land turns (protocol 2).** A drift refusal of a green, approved PR
may record `refs/land-turn/pr-<N>`, pointing at a commit with subject
`land-turn pr=<N>` and one JSON line containing `pr`, `head`, `acquired`,
`expires`, and `files`. The lifetime is 90 minutes; repeat refusals and changed
heads do not extend it. Legacy records infer acquisition as `expires - 5400`.
Acquisition time, then PR number, orders tickets; only the oldest eligible
non-overlapping holders are effective. Younger overlapping tickets wait and
never refuse the older holder. Acquisition checks that same ordering before
and after publication. A waiting ticket cannot reserve an extra area against
otherwise independent PRs. This is a preflight, not an atomic merge lock.

The reader follows all ref-list pages and fetches immutable commit objects in
batches of 100, rather than dropping old holders after six sequential reads.
Incomplete, timed-out or malformed snapshots **fail closed**: base drift checks
alone cannot establish fair turn ordering. Bounded record and eligibility reads
refuse rather than silently admitting when their deadline is reached. The
refusal reports both a hold and independently observed base drift so the owner
refreshes immediately rather than merely parking behind the holder.

**Shared time budget.** The state-merge preflight (and each state pre-push turn
check) runs under the canonical aggregate deadline described under "Preflight
budgets" (`PREFLIGHT_TIMEOUT`, 18 s inside the 20 s callback budget; there is
no second, shorter budget): every `gh`/`git` timeout is clipped to what is left,
an exhausted budget refuses with a named message (never allows), and no turn
write starts with less than 10 s left, so nothing is published after the
deadline. Within one invocation the snapshot is read once and reused (commit
objects by sha, holder PRs fetched concurrently on copied contexts, one compare
per base, no per-file attribution while deciding a yield); only the post-write
ordering check re-lists refs. If that re-read fails, the refusal says the turn
record was already written and may be in force.

An old ticket is not expired by age alone: the 15-minute grace below yields only
a holder that is still drifted against its current base, so a refreshed head
keeps its place (pinned in `tests_turn.py`).

Closed PRs and exact-head requests for changes yield. A changed head keeps its
ticket exactly when the merge gate's own `_approval_refusal` would still admit
it (exact-head approval, or the existing patch-identical update-branch carry;
that policy is not changed here); an unreviewed content change yields.
After a 15-minute refresh grace from the later of acquisition and sustained drift arrival, a still-drifted holder
yields as well; a refreshed head the gate admits can regain the original ticket only
within its original lifetime. CI may still be pending while an eligible holder
reserves its area. The usual approval, review and CI gates still apply to landing.

**Drift-age attribution (server push clock).** The 900-second grace for a drifted
holder runs from `max(acquired, T)`, where `T` starts the currently sustained overlap.
The validated `base...state` compare pins a unique tip. GitHub's state activity API
supplies the authoritative `before`/`after` chain back to the CI base and the server
push timestamps; commit parent order and commit dates never determine arrival.
Events newer than the frozen tip are ignored; a push straddling the base is clipped
to that base. Cycles, duplicate destinations, missing links, malformed events and
decreasing timestamps refuse.

For each push endpoint `after_j`, test the cumulative net diff `base...after_j`
with the normal overlap rule, including both rename paths. Reuse the already
validated overall compare for the tip. Walk endpoints newest backward until the
latest nonoverlap, then take the next push's timestamp; if every prefix overlaps,
take the first push's timestamp. Equivalently, choose the oldest `k` for which
every cumulative prefix `j >= k` overlaps. Thus a touch, revert, and new touch of
the same file (or a different overlapping file) starts a fresh full grace, not a
grace from the cancelled touch. At most eight immutable compare reads run at once;
results decide strictly newest-first. Truncated (>= 300 files) or unreadable
prefixes needed for that decision refuse. Workers are stopped and joined before
return. Eligibility's existing 30 s ceiling is also clipped by the canonical
18 s aggregate deadline (including standalone pre-push): the earlier deadline
bounds every subprocess and rejects late results.
Future selected timestamps refuse; no new clock heuristic or persistent clock is used.

**Retention limit:** activity is read serially by the API's `Link` next cursor,
not page number, for at most five pages of 100 events. GitHub does not document
activity retention. A base outside the available chain/page bound fails closed;
its holder cannot yield on drift age and may retain priority until ticket expiry.

After expiry there is a further 90-minute cooldown before reacquisition. Cleanup
is limited to two past-cooldown records per snapshot. A malformed open peer
record names its ref and refuses; the owner can repair its own record through
normal acquisition (after checking other holders), or close/merge its PR.
Malformed closed records do not block. No peer's live malformed ref is edited
or deleted as recovery. State pre-push requires protocol 2 and invokes this same
reader and selector for direct non-export pushes, instead of reconstructing
eligibility from a recorded tree. Deploy the plugin before the state wrapper.

The earlier single-contender history replay reported 97.2% / 96.2% landing
within two cycles at 20 / 60 minutes respectively. Those numbers did not test
concurrent holders and are **not evidence of a concurrent landing bound**.
The original simulator belongs to card t_01a1d6b3538e8e43; a concurrent replay
and deployment proof are separate validation requirements.

**Gate boundary:** this guard checks that an exact-head approval record exists;
it does not authenticate that its author was independent (every agent posts as
the same GitHub account), and it does not prove coverage of required-but-absent
CI. Those remain the landing owner's mandatory gates, just as for existing state
pushes. Passing this preflight alone
is NOT approval to land. Main still requires `hermes-core-wt ship`; merge API
endpoints remain blocked. Do not set a trusted marker for state landing.
The head fence is atomic at GitHub; the base reads are only a preflight, not an
atomic base lock: a commit landing between the compare and the merge is not seen. Do not retarget a PR during landing; use server-side branch
protection against concurrent main retargeting.

To validate the route without changing the PR, run `guard.py --gh-args` with the
same arguments (see below). It only classifies; never invoke real `gh pr merge`
as a dry-run. After a genuine owner-controlled merge, read back MERGED/base/SHA
and verify postmerge CI and deployment separately.

`tests_state.py` covers allowed and refused preflights, malformed/unreadable
responses, transport failures, head/base changes, CI states, bounded rollups,
base drift under CI (the #2491 replay, sync churn, workflows, CI harness files,
shared support files, test<->code coupling, per-file attribution, truncated compares),
strict flags, `cd $VAR;` prefixes, named failure steps and terminal/Python/tool
entry points. No test merges a live PR.

A command in the state-merge form that fails the preflight names the failing step
(`gh pr view` exit/stderr or transport error, state/base/head/url mismatch, the
non-green check names and conclusions) instead of the generic deny. The `gh`
probes take the repo from the explicit `--repo`, so a derived cwd that does not
exist (e.g. an unset `cd $VAR;` target) is dropped and the probe runs in the
guard's own cwd; `cd $VAR`/`${VAR}` targets expand from the command's env when set.

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
complex shell expansion/substitutions, variable-based cwd beyond a plain
`$VAR`/`${VAR}` cd target set in the env snapshot, conditional shell state,
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
