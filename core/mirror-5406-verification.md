# Mirror 5406 verification

## Frozen inventory and unchanged verifier

Appended 367 unique source commits as ordinals 5040–5406, including the optional lanes-flow reader injection seam at 5403. Source snapshot: `e0a99816ec0fa3a1f01aebf20d1a161e51b0ddd3`. Exclusion base: `ea405e49a5e281558b3eeb3a13af66c6116cd0f2`. No later source arrivals are included.

The unchanged `core/verify_mirror.py` CLI exited 0 using `mirror-policy-5406.json` and `mirror-replay-5406.json`, reporting:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5406, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "e0a99816ec0fa3a1f01aebf20d1a161e51b0ddd3"}
```

Frozen policy SHA-256: `ab1af1bda88ffb03d438757e66ff61da4c1201b1a1f668de7339ffdafa949d77`.

## Actual Applying recipe

Executed the README's segmented Bash recipe sequentially after the CLI, with Git 2.51 and a disposable source worktree. The recipe applied all 5406 mails with the existing 51 bridges and three ancestry-only markers, then performed the mandatory additive hostname-escape repair. Exit status was 0. Final repaired-tree checker output:

```json
{"marker_compile": "pass", "overlay_blob": "4c449aac02760ef58add6ae94cc9044a16b9831d", "source_tip": "e0a99816ec0fa3a1f01aebf20d1a161e51b0ddd3", "whole_tree": "pass"}
```

The repair requires exact preimage `b5429a04c4d65c0570280a84c9a0f3d2be625eb6` and produces the above postimage. Eight snapshot-overlay unit tests passed. Historical mails remain immutable; the repair is not inserted into their context.

## Preservation, normalization and publication checks

- Independently hash-checked 5090 previously published patch, bridge and verifier files against the baseline: all byte-identical. No published ordinal or source header was changed.
- Verified 367 new unique SHA headers, exactly one unescaped mail separator in each new mail, contiguous inventory and complete feature-document coverage. Added 104 feature documents and README coverage rows.
- The 236 policy fingerprints were derived from normalized source plus inherited and newly reviewed exact line mappings, not arbitrary replay differences. New replacements remove private fixture identifiers and incident prose while preserving operational protocol literals and historical context. Global fixture mappings were checked for consistency and collisions. Description-only identifiers were audited separately because tree equivalence does not inspect mail prose.
- Full private-denylist scans cover new mails, documents, policies, repair helpers and filenames; the original denylist was not weakened. A broader review also inspected incident timestamps, transcript identifiers, mixed-case repository fixtures and session placeholders.
- Ten inspected gitleaks detections in synthetic argv/API fixtures and public command examples received only exact file/rule/line ignore entries. No broad rule suppression was added.
- `core/check_coverage.py`, the staged pre-commit privacy/gitleaks gates and whitespace checks for Markdown/JSON/Python passed. Mail and bridge context whitespace is intentionally excluded from whitespace checks.

## Feature-test evidence boundary

The feature documents identify source tests and distinguish producer-reported results from execution in this mirror pass. In particular, the lanes-flow reader-injection producer and independent source reviewer reported 84 passing tests. This pass verifies mirror inventory, replay, normalized full-tree equality and the repair tests; it does not claim to have re-executed those feature suites. The injection seam is core-owned; a background reader's lifetime remains plugin-owned.
