# Mirror 1979 verification

Frozen source tip: `af81aae2d90e2df708f9b4e725e740bcdc4d385e`.
Upstream exclusion and current merge-base: `71736c9f32597bbca9d89cede5aa136e55946b2f`.

47 missing SHA headers are appended as 1933–1979 in reverse non-merge rev-list order. All 1932 previously published patch files retain their exact bytes and ordinals. The verifier and coverage checker are unchanged. Feature documentation: [1933–1979](mirror-1979-features.md).

## Policy and integration bridge

The new policy copies `mirror-policy-1932.json`, changing only its independent `source_tip` binding. Literal/regex normalization, all fingerprints and contributor-path renames are unchanged. The new replay plan also updates its tip and preserves all sixteen prior bridges and three ancestry-only entries.

A seventeenth bridge, `bridges/upstream-through-c79ab4146a8.diff`, precedes patch 1975 (`2809ac61c7cd81be62792cac3fdf0c728afb8172`). It carries the integration of upstream through merge `c79ab4146a8dd141ef57d4af6702119e90bdd59c`, including the fork's inner merge resolutions. Its SHA-256 is `38ec1e585bb3a276e0c7ea0da2e690c599830d0a9d8e7036e230f680f58a11d6`.

The bridge was constructed in source space: replay the new source patches through 1974 on the preceding frozen tip, then diff that tree against the integration merge. The reconstructed starting tree is `35ee3d53` (abbreviated Git tree ID); it contains mainline changes plus the three sync-branch commits already represented by patches 1933, 1937 and 1939. Independent adversarial review reconstructed that starting tree, checked the complete diff and boundary, and confirmed the outer merge adds no manual resolution of its own. The bridge touches the same 213 paths as the inner upstream integration; it is not a final-tree replacement or a policy exemption. No published mail was rewritten.

## Executed inventory/replay/tree verifier

This unchanged command exited **0** against the shared source object store:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip af81aae2d90e2df708f9b4e725e740bcdc4d385e \
  --base 71736c9f32597bbca9d89cede5aa136e55946b2f \
  --patch-dir core/patches --policy core/mirror-policy-1979.json \
  --replay-plan core/mirror-replay-1979.json
```

```json
{"ancestry_only": 3, "base": "71736c9f32597bbca9d89cede5aa136e55946b2f", "bridges": 17, "merge_base": "71736c9f32597bbca9d89cede5aa136e55946b2f", "patches": 1979, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "af81aae2d90e2df708f9b4e725e740bcdc4d385e"}
```

It verifies contiguous ordinals, unique single-mail SHA headers, exact source inventory equality, ancestry-only source proofs, hash-pinned bridge replay and normalized full-tree equivalence. A separate index preflight checked **11,285** distinct nonzero IDs. Three abbreviated historical IDs remain unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`. Successful full replay did not require their three-way fallback preimages.

## Tests and publication scope

The actual README Applying Bash block was extracted and executed unchanged (exit **0**), including its own full verifier invocation. Its resulting commit `7a71ab1f2c916acc467fed998f98878c5d86b7e2` separately passed `verify_mirror.compare` against the frozen source under the same policy.

On both the frozen source and README-applied tree, all **47** changed Python test files ran with the canonical `scripts/run_tests.sh -j 1` runner under isolated homes: **2185 passed, 0 failed, 12 skipped per tree**, exit **0**. This includes the six classic CLI live-session harness cases. OS-specific cases are skipped on Linux. The exact file inventory is listed in the [feature document](mirror-1979-features.md).

The changed JavaScript suites also passed separately on both trees with Node 26.12.1 and Vitest 4.1.12: **9 Desktop files / 62 tests**, plus **1 TUI file / 9 tests**, all exit **0**. Each tree used its own lockfile-based `npm ci --ignore-scripts` installation; sharing only root dependencies initially missed a workspace-local Babel plugin and failed during configuration before tests started. Installing each complete workspace fixed that prerequisite without changing source or tests.

Staged `check_coverage.py` exited **0**, the private denylist scan found **0** hits across all 53 changed files, and gitleaks staged scanning found no leaks. No full-suite, native Windows/macOS, live Desktop GUI, production Board or deployed gateway behavioral proof is implied by mirror equivalence. Existing line-scoped scanner exceptions remain unchanged; no new synthetic-test exception is needed for this batch.
