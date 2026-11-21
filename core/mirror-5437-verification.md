# Mirror 5437 verification

## Frozen inventory and unchanged verifier

Appended 31 unique source commits as ordinals 5407–5437, in `git rev-list --reverse --no-merges` order among themselves. Source snapshot: `a61ed2bc32bef9f4cb2bf8fde409093776fb7505`. Exclusion base: `ea405e49a5e281558b3eeb3a13af66c6116cd0f2` (unchanged). The range contains no merge commits, so no new bridge is required (51 bridges and three ancestry-only markers retained). No later source arrivals are included. Published ordinals 1–5406 and all previously published patch, bridge and verifier bytes are unchanged; the only edited tracked file is `README.md`.

The unchanged `core/verify_mirror.py` CLI exited 0 using `mirror-policy-5437.json` and `mirror-replay-5437.json`, reporting:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5437, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "a61ed2bc32bef9f4cb2bf8fde409093776fb7505"}
```

Frozen policy SHA-256 values (frozen before the final CLI run): `mirror-policy-5437.json` `34a095d0617b7c43609f9bbd9048628f7c2ee9a16da65da397c9f2a85ae027de`; `mirror-policy-5437-escape-repair.json` `1f115d1315bc169b1b8bc1e4140e3047feb0a832859c05f3fdf1f95f2c124c71`; `mirror-replay-5437.json` `700c408c8f0eb7d6e90a189792fa74cf0328890d7c54c4e2a1d3dbc986ac18f7`.

## Fingerprint re-pins

Eight already-fingerprinted paths were touched by the new range. For each, the new applied blob was proven to equal the normalized new source plus exactly the line mappings of the previous fingerprint (identical hunk lists, no additional differing hunk), and the two blob IDs were re-pinned in both the policy and the escape-repair policy. No new fingerprint and no rename rule was added; the repair policy differs from the main policy only in the existing overlay postimage entry.

## Actual Applying recipe

Executed the README's segmented Bash recipe sequentially (it runs the verifier CLI first), with Git 2.51 and a disposable source worktree. It applied all 5437 mails with the 51 bridges and three ancestry-only markers, then the mandatory additive hostname-escape repair. Exit status 0. Final repaired-tree checker output:

```json
{"marker_compile": "pass", "overlay_blob": "4c449aac02760ef58add6ae94cc9044a16b9831d", "source_tip": "a61ed2bc32bef9f4cb2bf8fde409093776fb7505", "whole_tree": "pass"}
```

The new snapshot-bound repair copy (`repair_hostname_escapes_5437.py`, its policy and test) has the same BEFORE/AFTER blobs as the 5406 copy because `hermes_cli/kanban_placement.py` is untouched by this range. Its eight unit tests pass.

## Privacy and publication checks

- Full private-denylist scans (original denylist, not trimmed) cover the 31 new mails, filenames, 11 feature documents, policies, replay plan, repair helper and this document. Two description/comment hits were normalized: a product name in one mail description, and a private laptop name in one mail (applied through the policy's existing hostname regex rule, so the whole-tree comparison still holds). Chat-id prefixes and Kanban card ids in the new mails follow the existing published precedent and were not rewritten.
- Each new mail has exactly one unescaped mail separator; header SHAs are unique and the header set equals `rev-list --no-merges base..tip`.
- 990 historical index blobs missing from a fresh full clone were fetched read-only from the documented hub wrapper (hash-checked); 35 abbreviated or obsolete preimage IDs remain unavailable, and the complete replay did not need them.
- Feature documents: 11 new, one README row per new patch, `core/check_coverage.py` passes.

## Feature-test evidence boundary

Feature documents identify source tests and describe their coverage; they do not claim the mirror pass executed them. For the completed-card host peek (5419–5420), producer-reported results were 518 passed/1 skipped (original consumers), 241 passed/1 skipped (provenance-corrected), 19 final focused and 44 independent focused; none were re-executed here, and no exact-source idle-green claim is made. This pass verifies inventory, replay, normalized full-tree equality and the repair tests only.
