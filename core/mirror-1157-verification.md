# Public core mirror: 1157-entry snapshot

## Frozen inventory

- Fork tip: `91edcccfb07ff63042a60d5fb7d1745c2ea8130f`.
- Upstream exclusion tip: `ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8`.
- Merge-base: `5608b5faf19ac22d023e62a8fd9675a42d1a61c6`.
- Inventory: 1157 non-merge commits, one mail patch per source SHA, numbered `0001` through `1157`.

This publication appends 23 entries, `1135–1157`. Full raw exports were regenerated into a temporary directory and their source headers compared with the existing directory. All first 1134 headers matched their canonical ordinal, so their existing curated patches were retained without trailer or filename churn. The public remote was fetched first; no other open mirror owner was found through cross-gateway task discovery.

Ten feature documents and the README rows cover the new entries, including patch 1154 (`0e47c674ec`), the custom-label launchd discovery and supervisor handback change. The earlier [1134 report](mirror-1134-verification.md) describes the inherited historical linearization and its integration overlay; these remain in place. These are not 1157 independently installable raw diffs.

## Executed replay and complete tree audit

A detached source-repository worktree was created at the frozen merge-base. The final replay used:

```sh
git am -3 --empty=keep /path/to/hermes-mods/core/patches/[0-9][0-9][0-9][0-9]-*.patch
```

Result: exit 0 across all 1157 mail patches, with no manual resolution during that final replay. Empty historical entries were kept. The upstream merge-base advanced since the prior publication only in `plugin-catalog/loreconvo.yaml`; no existing mirror patch touched that file.

The inventory function from `core/verify_mirror.py` checked all 1157 source SHA headers and contiguous unique ordinals against the frozen source log. A separate full-tree audit checked every path, mode, object kind and blob:

- Source paths: 22799; applied paths: 22799.
- Inherited contributor-marker filename mappings: 3.
- Content-normalized files: 32.
- Applied tree: `31e14e36ad348edac898352f9f3235646a5af124`.
- Unexplained differences: none.

The audit derived the inherited per-file public normalization from the frozen previous source and replayed 1134-entry trees. It applied those exact substitutions to the new source blobs, then allowed only this publication’s explicit example-identity substitutions on differing lines. Path mappings were paired by identical mode/kind/blob, not guessed filenames. Existing operational routing literals were preserved. The separate audit is necessary because the public verifier’s full `verify` command does not support the inherited normalized filenames; a full verifier invocation is not claimed.

## Targeted tests

Scope: the 31 test files changed by the new range (not the full suite, and no live macOS/launchd run), executed on the replayed public tree (merge-base plus all 1157 patches) with the repository runner:

`HERMES_PYTHON=<older repo venv> scripts/run_tests.sh -j3 --file-timeout 240 <31 files>`

Result: 31 files, 627 passed, 2 failed, 5 skipped in 860 s. The 5 skips are platform-gated (`macos` and `windows` lanes, skipped on this Linux host). The 2 failures were both in `tests/gateway/test_session_policy_refusal_fields.py` with `ModuleNotFoundError: No module named 'acp'`: the older venv used for the run lacks the optional `acp` package, a missing dependency and not a code defect. That file was rerun with the runner on a complete existing test interpreter that has `acp` installed: 50 passed, 0 failed. Counting the rerun in place of the first run of that file, all 31 files pass (629 passed, 5 platform skips). Neither source nor tests were changed to get this result.

## Privacy, coverage and publication checks

The author header is replaced with the public noreply identity. New mail descriptions and applicable comments/fixtures use example user, application and host identities. The 23 new patch files were each scanned in full, including removed/context lines, with the private denylist’s active case-insensitive extended expressions: all printed `:0` and grep exited 1 (no matches).

The staged coverage checker and normal public-repository pre-commit hook are required for publication. No `--no-verify` or unscanned bypass is used. Only this card’s core files are staged; unrelated live plugin edits are excluded. Code and test paths named in the new documents were checked against the replayed tree.

## Preparatory failure and countermeasure

Observed: the first preparatory replay stopped at 1150 with context/blob errors in capability-routing files. Comparing the pre-1150 replayed files with the source parent showed those files already matched exactly.

Why it happened: an extra blanket hostname scrub changed an operational routing literal in the exported patch, including its context, while the base correctly retained that literal. This made the modified patch inconsistent with its recorded base blob. Why the privacy check did not catch it: denylist scanning checks disclosure, not patch compatibility or runtime semantics. The existing mandatory apply check caught the problem before publication; no bad patch was shipped.

Countermeasures: restrict extra hostname scrubbing to mail descriptions and explicit example comments/fixtures, preserve operational protocol literals, repeat the entire replay from the merge-base, and audit the complete normalized final tree. The mirror skill now records that distinction so future exports do not repeat the broad substitution. Final replay exit 0 and the full-tree audit are the verification, not merely an edited patch that looks plausible.

No hosted CI result, full repository suite, live macOS launchctl operation or live cross-gateway notification is claimed by this mirror.
