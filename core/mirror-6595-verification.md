# Mirror snapshot 6595 verification

## Frozen scope

This append adds 646 non-merge source commits, ordinals 5950–6595, after snapshot 5949. The frozen source tip is `26aaccc5a5b4fb5c247825d92579efd0765a9596`; the exclusion base is `ea405e49a5e281558b3eeb3a13af66c6116cd0f2`. There are no new merges in this append. Previously published mail bytes and ordinals are unchanged. Eighty feature documents cover the append.

The sidebar changes are ordinals 6508–6512. They reuse the existing search-focus command, not a new binding. The separate page-navigation follow-up is not included. The final native-owner binding modules are inert read-only producers, not registered consumers or application authority. This is source and documentation publication, not installation or live delivery.

## Full inventory, replay and tree comparison

The unchanged verifier ran as the first step of the actual README Applying recipe. The complete recipe exited 0 on 2026-11-27 at 22:44:28 UTC, after starting at 22:25:30 UTC. Its Bash block SHA-256 was `cf6194ec7fc5c13c36c2f608630e9cd8c66be2f2e3fbe2f01abede44b8f12775`.

Verifier output:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 6595, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "26aaccc5a5b4fb5c247825d92579efd0765a9596"}
```

The recipe then independently applied the documented segmented mail/bridge sequence and mandatory exact-pre/postimage hostname escape repair. Final checker output:

```json
{"marker_compile": "pass", "overlay_blob": "7882a4980a3303ae0ed6536c836551657dbdf17a", "source_tip": "26aaccc5a5b4fb5c247825d92579efd0765a9596", "whole_tree": "pass"}
```

Both comparisons use the declared snapshot policy, not byte-for-byte private source equality. The escape repair remains explicit and does not rewrite historical mails. Git 2.51 was used. Historical missing index preimages that the replay did not need remain unavailable; complete replay succeeded without them.

## Privacy and normalization

New mails preserve every source SHA and complete diff inventory. Source-version reconstruction checked 2,743 nondeletion file versions, preserving prior public old-side/context bytes and deriving new lines from declared normalization. Optional descriptive hunk suffixes were removed. Incident-derived identities, timelines, narratives and attestation fields were normalized without withholding fields or hunks; affected policy fingerprints were derived from those exact transformations. The no-final-newline fixture was independently checked.

Forty corpus `source_sha256` values recompute from the final normalized canonical envelopes. A recovery operation identifier is synthetic while retaining the exact equality fence. The corpus also contains upstream-redacted opaque idempotency dedupe tokens. These are not credentials, but their original derivation is not independently reproducible from available source; they are **not** claimed as newly derived synthetic values. Independent review allowed exactly 31 file/rule/line-scoped gitleaks exceptions for those tokens in patch 6156. A copy scan found 31 findings before the entries, zero after, and one finding when one entry was removed. No path-wide or rule-wide exemption was introduced.

The actual private denylist, whole-file staged scan, normal staged gitleaks/pre-commit hook and coverage checker are publication gates. Passing a denylist alone is not proof of privacy. Independent source/privacy reviews found and prompted corrections before this final candidate.

## Tests and limits

- Verifier and snapshot escape-repair unit tests: 36 passed.
- Nine high-risk source fixture test files on the expected-public normalization overlay: 341 passed, no failures or retries.
- Eleven corrected fixture test files on that expected-public overlay: 227 passed, no failures or retries. These overlap earlier coverage and are not an additive unique total.
- Separate witnesses checked the recovery identifier fence and all 40 recomputable envelope digests.
- Coverage and documentation/JSON whitespace checks passed. Mail/bridge context whitespace is intentionally excluded from documentation whitespace checks.

The fixture tests used expected-public source overlays, **not** the final recipe worktree; the full replay comparisons separately prove its declared tree. Source-diff documentation review is not runtime testing. Feature documents explicitly distinguish untested entry paths. Prior sidebar-owner evidence reported 107 tests across nine files plus typecheck/build; that is reported prior evidence, not rerun here. Electron failures reproduced on the baseline are not described as all-green E2E. No exhaustive application suite, native activation, deployment, restart or live acceptance is claimed.
