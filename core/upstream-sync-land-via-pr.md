# Land upstream syncs through a PR and retire superseded candidates

Patches: 1293, 1295, 1296, **1442** (`d84f34a9c9f6`),
**1443** (`548d136b7000`), **1463** (`3afbed9bb882`).

## Behavior

- The landing gate opens or reuses a PR from `sync/upstream-<head>`, requires **All required checks pass (v2)** on that exact head, and requests a merge pinned to it without an admin override. Missing/pending evidence returns NOT_YET; failed/cancelled evidence or branch-protection refusal returns REFUSED. Protection errors retain GitHub's diagnostic message (1293, 1295); 1296 documents PR-only landing.
- 1442 runs superseded-PR cleanup after the gate's decision, including a bare invocation without a candidate. Same-repository sync PRs targeting main are eligible only when their named head is still the remote branch head, is a merge, and every parent is already on freshly fetched main. Eligible PRs receive an explanatory comment, are closed, and have their branches deleted with a SHA lease. Cleanup failures do not change the gate verdict.
- 1443 applies that same all-parent supersession rule before staging or landing a candidate: even a merge with unique conflict-resolution content is considered superseded when all its parents are on main. It is neither re-staged nor landed.
- 1463 also removes superseded orphan staging refs under the sync prefix. Any open PR on any base protects its head ref. Candidates this invocation is staging or landing are protected, as are refs already handled by the PR cleanup. Remote movement after listing causes leased deletion to fail rather than delete the moved branch.

## How it works

`scripts/ci/upstream_sync.py::advance` collects protected candidate SHAs while `_advance` determines the result. `_parents_on_main` requires a merge (at least two parents) for the candidate short-circuit. `land` protects a candidate before opening its PR, including a failed create attempt; staging protects it before `_stage_or_wait`.

The housekeeping adapter imports `upstream_sync_superseded.py` and catches errors without replacing the result. Its PR path checks repository identity, exact prefix-plus-SHA ref naming, current remote SHA and ancestry before comment/close. Branch deletion uses `--force-with-lease=<ref>:<checked-sha>`.

The orphan path lists all open PRs, then remote heads under the sync prefix. It excludes their refs, protected SHAs and their constructed refs, plus every ref considered in the PR pass. It fetches missing objects and checks all listed head parents against the fetched main before leased deletion. Unlike the PR path, this path accepts any nonempty parent list, not only merge heads. Per-PR and per-branch faults are logged and do not stop subsequent entries; a prerequisite list/fetch failure prevents unsafe cleanup.

## Code

- `scripts/ci/upstream_sync.py`: `land`, `_parents_on_main`, `_advance`, `advance`, `_close_superseded`.
- `scripts/ci/upstream_sync_superseded.py`: `_kept_because`, `_close`, `_delete_orphans`, `close_superseded`.
- `.github/LANDING.md`; test support in `tests/hermes_cli/upstream_gate_fake.py`.

## Tests

Source tests inspected; **not rerun in this mirror pass**. These are local Git histories/remotes and simulated GitHub adapters, not live GitHub cleanup or landing.

- Earlier coverage in `tests/scripts/ci/test_upstream_sync.py`: protected-main PR-only landing, exact-head required check, GH006 refusal wording, protected staging and protection-error recognition. Updater tests in `tests/hermes_cli/test_update_fork_merge_upstream.py` and `test_update_fork_sync_identity.py` were adjusted by the earlier patches. `.github/LANDING.md` itself has no behavioral test.
- 1442 `tests/scripts/ci/test_upstream_sync_superseded.py`: `test_landing_a_sync_closes_its_superseded_twin_and_keeps_an_unlanded_one`, `test_bare_call_closes_only_same_repo_sync_merges_whose_parents_main_has`, and `test_github_failures_are_logged_and_the_gate_continues` cover cleanup after landing and bare calls, eligibility boundaries and error isolation.
- 1443 in that file: `test_superseded_resolution_is_not_reopened_or_landed` exercises distinct resolution content with already-landed parents; `test_missing_fork_parent_keeps_sync_open` preserves a candidate lacking a landed parent.
- 1463 `tests/scripts/ci/test_upstream_sync_orphans.py`: orphan deletion and missing-parent retention; staged candidate protection with concurrent main/ref movement; moved orphan lease refusal with continued cleanup; first-comment exceptions/nonzero results not stopping the second PR; protection after landing-PR creation failure and concurrent landing; and an open release-target PR vetoing orphan deletion.

## Applying

Use the complete numbered, bridge-aware series and replay instructions in [README.md](README.md), preserving 1293/1295/1296 and intervening gate changes before 1442 → 1443 → 1463. The housekeeping module must ship with the gate. An adopter must provide appropriate PR and staging-ref permissions and adapt the required check name and sync prefix to its own policy. Applying patches neither changes repository protection nor performs cleanup by itself.

## Gotchas

- All-parent supersession is **policy, not tree equality**. Unique resolution content can intentionally be discarded by this rule. Missing even one parent prevents it.
- A lease protects branch deletion against a changed SHA; GitHub comment/close operations are not an atomic transaction with that deletion. A PR may be closed while its branch remains after a lease or push failure.
- The orphan scan protects open PR refs on any base and the current call's candidates. It does not promise global locking against all concurrent PR creation.
- Cleanup is best-effort and leaves the landing verdict unchanged. Read diagnostics as well as the exit code.
- Never use `--admin` to bypass the exact-head gate.

## Plugin?

No. This is repository CI gate and Git/GitHub housekeeping code.
