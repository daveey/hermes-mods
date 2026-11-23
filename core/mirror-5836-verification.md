# Mirror 5836 verification

## Frozen inventory

Appended 399 unique non-merge source commits as ordinals **5438–5836**, in `git rev-list --reverse --no-merges` order among the unpublished headers. Frozen source: `33774b3fc6826f1bd028a60b5c31963caa99da8c`; previous snapshot: `a61ed2bc32bef9f4cb2bf8fde409093776fb7505`. The exclusion base and merge-base are unchanged. There are no merges in this append; all 51 published bridges and three ancestry-only entries are retained unchanged. No subsequent source arrivals are included.

All previously published mails, bridges, verifier and coverage-checker bytes are unchanged. Existing tracked changes are limited to `README.md` and two narrow `.gitleaksignore` entries; all other deliverables are new snapshot files and feature documents.

## Unchanged full verifier

After freezing both policies and the replay plan, the unchanged CLI exited 0 with Git 2.51:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5836, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "33774b3fc6826f1bd028a60b5c31963caa99da8c"}
```

This proves contiguous ordinals, unique source headers, exact header-set equality with the frozen non-merge range, ancestry-only source-parent checks, ordered replay, and normalized whole-tree equality. Each mail has exactly one unescaped mbox separator.

Frozen SHA-256 bindings:

| File | SHA-256 |
|---|---|
| `mirror-policy-5836.json` | `c65356b1bed08cff9cb7222198574593ef8561bfb4c3b8998250487696016fc6` |
| `mirror-policy-5836-escape-repair.json` | `d4a4aa600e087ce71709284b9703403051c6656ce1899aa451d07d86e3219b8b` |
| `mirror-replay-5836.json` | `04a4cd7d9ca585103f1ae4b7546c7ebba0a43d542d6d9c366a5626d84ded483e` |

## Actual README Applying recipe and mandatory repair

Executed the CLI above, then the README's segmented Bash recipe sequentially against the same frozen source object store in a disposable worktree. All 5836 mails, 51 bridges and three ancestry-only markers applied; the final array slice is `4497:1339`. The additive snapshot-bound repair and repaired-tree comparison both exited 0:

```json
{"marker_compile": "pass", "overlay_blob": "6324662aaa1cf31fb3863d5a3b4b4ccbda095df7", "source_tip": "33774b3fc6826f1bd028a60b5c31963caa99da8c", "whole_tree": "pass"}
```

The placement file changed in this source range. The snapshot repair therefore pins historical applied preimage `bb44e2c35d746d73790e9762c63dd9d84d34c5fc` and repaired postimage `6324662aaa1cf31fb3863d5a3b4b4ccbda095df7`. Eight unit tests in `test_repair_hostname_escapes_5836.py` pass. The repair policy differs from the main policy only in that applied placement fingerprint.

Historical normalized patch-index blobs were supplied before replay. A final batch preflight checked 28,975 distinct nonzero index IDs: seven obsolete/abbreviated preimages remain unavailable. Neither complete replay required them; their absence was not hidden or waived.

## Public normalization and privacy boundaries

All 53 previously fingerprinted paths touched by this append were re-pinned from the normalized new source plus precisely the old reviewed line mappings. Those transformations were independently reproduced. Additional exact-line privacy transformations and public illustrative-fixture hash substitutions account for 56 new fingerprints; no normalization rules, rename rules, verifier logic or published bytes were weakened.

Private host/deployment identities, account/profile aliases, incident session/card identifiers, transcript receipt IDs and identifying live-incident prose in new descriptions and added lines were generalized. Task pseudonyms use a global deterministic injective mapping disjoint from existing fixture IDs. Existing compatibility/protocol identifiers remain unchanged. Private transformation reports and source snapshots are not part of this publication.

The plugin-review fixture is now explicitly an **inert sanitized public example**, not an assertion of original private archive approval or authorization to deploy. All 14 fixture-file digests, both registry maps and the subset-manifest binding were re-derived from sanitized bytes. On the final applied tree, the registry/file validator accepted every fixture and rejected all 14 one-byte mutations:

```json
{"fixtures": 14, "one_byte_rejections": 14, "plugins": 2, "production_imports_or_activation": false, "subset_manifest_binding": "pass"}
```

Full private-denylist scans cover the new mails, filenames, documents, policies and helper files. The pre-commit privacy hook passes with the original untrimmed private denylist. The two line-scoped gitleaks exceptions were inspected: one ordinary fixture SHA-256 string and one explicitly synthetic test sentinel, neither a credential. Documentation/JSON/Python whitespace checks are separate from intentional mail context/trailer whitespace.

## Coverage and requested feature

101 feature documents have Behavior / How it works / Code / Tests / Applying / Gotchas / plugin? sections and README coverage rows. `core/check_coverage.py` passes with the new files staged. No hotkey is introduced in this append.

The requested canonical caller-owned acquisition feature is documented in [mirror-5836-d-canonical-session-acquisition.md](mirror-5836-d-canonical-session-acquisition.md), with these exact source headers:

| Ordinal | Source commit |
|---|---|
| 5834 | `e75617ea199ad84b2e290548fe7d2f915854edcd` |
| 5835 | `17ab08071d03638060f39476efcfeb2a93037b88` |
| 5836 | `33774b3fc6826f1bd028a60b5c31963caa99da8c` |

Feature documents identify source test coverage; the mirror pass does **not** claim to execute those source suites or prove live behavior. Executed acceptance here is inventory, normalized full-tree replay, the actual recipe/repair, eight repair unit tests, fixture digest/one-byte rejection checks, privacy hooks and documentation coverage. No source implementation or runtime activation was performed.
