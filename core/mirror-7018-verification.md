# Mirror snapshot 7018 verification

## Frozen scope

This append adds 423 non-merge source commits, ordinals 6596–7018, after snapshot 6595. Frozen source tip: `6eb51e04afd24f29d8126dd22b29fc28d3060ed6`. Exclusion base: `ea405e49a5e281558b3eeb3a13af66c6116cd0f2`. There are no new merges. All 6,595 published mails and 50 unique historical bridge files remain byte-identical; the replay plan contains 51 bridge entries and three ancestry-only markers. Twenty-seven new feature documents and the existing sidebar-search document cover this append.

Ordinal 7018 removes the sidebar page-navigation block and its reserved space without removing the page registry, palette, shortcuts or plugin behavior. Existing sidebar search disclosure and New Session behavior remain documented in the existing feature document. This is source publication, not installation or live delivery.

## Full inventory, replay and tree comparison

After the independent privacy-review correction batch, the unchanged full verifier exited 0 at `2026-11-28T05:43:31.490145+00:00`. The actual README Applying Bash then ran from `2026-11-28T05:43:31.563602+00:00` through `2026-11-28T06:23:12.324487+00:00` and exited 0. Its extracted Bash SHA-256 was `50cc598002c59dca088725925e9a8e8fbef27430dd0f66a8db8bcff3530539da`.

The recipe first reran the complete inventory/replay/tree verifier:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 7018, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "6eb51e04afd24f29d8126dd22b29fc28d3060ed6"}
```

It then independently applied the documented segmented mails/bridges/ancestry markers, including the final 2,521-mail slice, and the mandatory exact-pre/postimage escape repair. Final checker output:

```json
{"marker_compile": "pass", "overlay_blob": "45d6d2ee87a6fbd9dcfdc23485030b5906289636", "source_tip": "6eb51e04afd24f29d8126dd22b29fc28d3060ed6", "whole_tree": "pass"}
```

Git 2.51 was used. Both comparisons use the declared normalization policy, not private-source byte equality. The policy has 482 fingerprint entries. Historical escape repair remains an explicit post-replay overlay; published mails were not rewritten. Unavailable historical index preimages not needed by the successful replay remain unavailable.

## Privacy and normalization

All 423 new mails match the frozen tested candidate. Source-version reconstruction recorded 2,070 proofs while preserving historical public context. Exact-line privacy transformations remove incident identifiers, timelines, narratives and attestation correlators while retaining tested custody and episode semantics. Encoded fixture payloads and their bound digests were normalized together. Six serialized JSON Unicode escapes remove accidental denylist matches without changing parsed JSON or decoded payload bytes. The review correction batch also removes added private task/review provenance, replaces 29 generated fixture identities with a globally consistent injective equal-length synthetic mapping, and recomputes three bound intent digests. Exact previous-applied-to-corrected-applied proofs cover 13 paths with source blobs unchanged. Production validators pass across three fixtures and five nested intent/digest pairs; no assertions were weakened.

An independent decoded-payload scan checked 320 unique payloads totaling 2,777,675 bytes against all 18 private denylist patterns with zero hits. Twenty-four exact file/rule/line-scoped gitleaks exceptions were independently checked as public fixture digests matching their decoded payloads, not credentials. No path-wide or rule-wide exemption was added. Full public-core gitleaks, staged privacy hooks, whole-file private denylist scanning and coverage checks passed before publication. A clean denylist alone does not establish privacy; source, prose and encoded-fixture review were also performed.

## Tests and limits

- Verifier and snapshot escape-repair unit tests: 36 passed.
- Expected-public policy-overlay semantic tests: one complete post-correction run across 45 files, 994 passed, 0 failed and 47 skipped, from `2026-11-28T05:09:17.229029+00:00` through `2026-11-28T05:47:24.899612+00:00`. The 43 spend-allowance Board cases require an isolated local wrangler Board, and four composed owner-release cases require its separate local Board; neither service was started for this mirror, so those cases are explicitly untested here. Earlier dependency-recovery attempts are historical evidence, not substituted for this final run. No assertions were weakened.
- Documentation/JSON/Python whitespace checks and coverage passed. Mail and bridge context whitespace is intentionally excluded from documentation whitespace checks.

The semantic tests ran on the expected-public policy overlay, not the final Applying worktree; complete replay and repaired-tree comparisons separately prove the declared output tree. Referenced source tests in feature documents are not all freshly executed by this mirror. Prior sidebar-owner build, native and review evidence remains prior evidence, not a fresh mirror execution. Known Electron failures reproduced on the baseline are not all-green E2E. No exhaustive application suite, Desktop build, native activation, restart, deployment or live acceptance is claimed.
