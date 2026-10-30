# Public core mirror: 1299-entry snapshot

## Frozen inventory

- Source tip: `1e40faadc04282fa17694def89b6e0f1c51847d4` (fork main after PR #331).
- Upstream exclusion tip: `2d2808ee84549f9836b0056750265058e5488a8a`; merge-base `5608b5faf19ac22d023e62a8fd9675a42d1a61c6`.
- Previous snapshot: [1281](mirror-1281-verification.md). Published ordinals 0001-1281 and 1283-1299 are unchanged.
- Correction: the first 1282-1299 publication (`70bf2b6`) put a second copy of 1281 (`9117ffc398`) at 1282 and left
  out `3c2af6ee2e` ("test(e2e): keep attach probe off unrelated PM provisioning", merged with PR #312). 1282 is now
  that commit, documented in [attach-probe-node.md](attach-probe-node.md). [kanban-supervisor-requests.md](kanban-supervisor-requests.md)
  covers 1278-1281 only.
- The 1299 SHA headers are unique and equal the set of `git rev-list --no-merges base..source-tip` (1299 commits).
- Policy: [mirror-policy-1299.json](mirror-policy-1299.json).

## Executed checks

```sh
python3 -B core/verify_mirror.py --source-repo <fork clone> \
  --source-tip 1e40faadc04282fa17694def89b6e0f1c51847d4 \
  --base 2d2808ee84549f9836b0056750265058e5488a8a \
  --patch-dir core/patches --policy core/mirror-policy-1299.json
```

Exit 0 (82 s on the hub):

```json
{"base": "2d2808ee84549f9836b0056750265058e5488a8a", "merge_base": "5608b5faf19ac22d023e62a8fd9675a42d1a61c6", "patches": 1299, "source_tip": "1e40faadc04282fa17694def89b6e0f1c51847d4"}
```

So the inventory holds, all 1299 entries replay in published order with `git am -3 --empty=keep`, and the applied
tree equals the source tree up to the policy. The source repository was a fresh bare clone of the fork with the
long-lived hub checkout's objects as an alternate. Without them, replay stops at 0378, a curated overlay patch whose
`index` preimage blob is not reachable from fork history (see `README.md` § Merge-aware mirror verification).

The policy is the whole public normalization of this tree, and the same 37 paths as the 1253 and 1281 snapshots
(34 modified, 3 renamed). 1282-1299 added none:

- `substitutions`: the mirror procedure's three scrub pairs, in sed order. They explain 12 of the 34 modified paths.
- `fingerprints`: the other 22 modified paths, as exact source/applied blob pairs. Each was read line by line
  after the substitutions. All are inherited public normalization: example IPs and hostnames, example product and
  laptop names, example launchd labels and AWS profile names, example session ids and chat text in test fixtures,
  and test strings rewritten so the secret scanner passes (`api_key="test"`, a parenthesized literal).
- `renames`: the three contributor-marker files under `contributors/emails/`, whose source names are private.
  This snapshot added `renames` to the policy format: a rule names only the public path and pins the source
  side by directory, mode and blob.

RED evidence for the fixes in this publication:

- The previous verifier on this corrected series: `Source header mismatch at ordinal 1167`. It required header i
  to be the i-th rev-list commit, which merged-branch appends break. The `70bf2b6` series fails the same way.
- The new verifier on the `70bf2b6` series:
  `Duplicate source header 9117ffc398e0f81189f31c723b36832c16684533 at ordinals 1281 and 1282`.
- The new `core/check_coverage.py` on the `70bf2b6` tree exits 1 with
  `ordinals 1281 and 1282 share source header 9117ffc398...`. The previous one exits 0.
- The new fixture `test_merged_branch_commit_appended_after_later_mainline_commit_passes` fails on the previous
  verifier (`Source header mismatch at ordinal 1`). The duplicate and omission fixtures fail there with the wrong
  error. All 26 verifier and coverage fixtures pass on the new code.

The new 1282 patch was scanned with the private denylist (comment-stripped, case-insensitive): zero hits. So was
the policy file. The pre-commit gate also runs gitleaks and `core/check_coverage.py`.

## Why the bad 1282 was published

- Problem: the 70bf2b6 series had 1299 files but 1298 distinct source commits. 1281 and 1282 both named
  `9117ffc398`, and `3c2af6ee2e` had no patch.
- Why 1: the 1282-1299 pass numbered new patches by position in `git log --reverse --no-merges` (1282-1299).
  `3c2af6ee2e` reached main in a merge at 09:19 PDT, after the 1281 snapshot was pushed. Its older date sorts it at
  position 1259, inside the published range, so position-numbering skipped it, and position 1282 was `9117ffc398` again.
- Why 2: skill hermes-mods-repo prescribed that position loop. The README policy (published ordinals never move;
  append unpublished SHA headers) was not in the skill. The skill now identifies each patch by its SHA header and
  appends only unpublished SHAs.
- Not caught 1: `core/check_coverage.py`, the only automatic gate, never read SHA headers.
- Not caught 2: `core/verify_mirror.py` compared header i with the i-th rev-list commit. Every snapshot since
  merged-branch commits started being appended failed it at ordinal 1167, so passes skipped the verifier and checked
  by hand. The 1299 pass also skipped the tree comparison, which would have shown the missing test file.
- Countermeasures: the verifier checks header-set equality, uniqueness and contiguity, and names duplicates and
  omissions. The coverage gate refuses duplicate headers on every commit that touches `core/`. The skill names the
  verifier's exit 0 as each pass's acceptance step. This snapshot is recorded with a reusable policy file.

## Boundaries

Source mirror and documentation only. The 1282 tests were not rerun here (they ran in the fork's CI for PR #312).
No running gateway, dispatcher or Desktop deployment is certified.
