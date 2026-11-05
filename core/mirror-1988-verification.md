# Mirror 1988 verification

Frozen source tip: `4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef`.
Upstream exclusion tip: `d8c79e4b6b8c63e6c40167ff95ec28e7b1fee189`.
Current merge-base: `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df`.

Nine missing non-merge SHA headers are appended as 1980–1988 in reverse rev-list order. All 1979 published patches retain their exact bytes and ordinals. Both verifier scripts remain unchanged. Feature documentation: [1980–1988](mirror-1988-features.md), including [exact named-profile homes](exact-profile-multiplexer-homes.md) in patch 1985 (`2ae1c978d138e8a0580a7f3b5c0c44ff1e07fed9`).

## Policy and integration bridge

The policy copies `mirror-policy-1979.json`, updating only `source_tip` and `base`. All literal and regex normalization, fingerprints and path renames are unchanged. The replay plan updates the same independent bindings and preserves seventeen bridges and three ancestry-only entries.

The eighteenth bridge, `bridges/upstream-through-41f4b65b573.diff`, precedes patch 1983 (`5417a9eae1dcfb72c64590f4995092b768c4456b`). Its SHA-256 is `9364ecbc826c9a43278a37370f2997f1285fe9d361bf625ed9b7150807868e01`.

It is the complete first-parent diff of source merge `41f4b65b5737179508c60f4ee20160cb51711dd4`, including the inner upstream integration resolutions. Its first parent is the source of patch 1982 and the merge is the parent of patch 1983. The standard normalization rules make no changes to this diff. Independent adversarial review reconstructed the diff byte-for-byte, checked its hash and boundary, and confirmed the outer merge's tree equals the automatic merge tree. This is a source integration bridge, not a final-tree replacement or a new comparison exemption.

## Executed inventory/replay/tree verifier

The unchanged command exited **0**:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef \
  --base d8c79e4b6b8c63e6c40167ff95ec28e7b1fee189 \
  --patch-dir core/patches --policy core/mirror-policy-1988.json \
  --replay-plan core/mirror-replay-1988.json
```

```json
{"ancestry_only": 3, "base": "d8c79e4b6b8c63e6c40167ff95ec28e7b1fee189", "bridges": 18, "merge_base": "ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df", "patches": 1988, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef"}
```

This proves contiguous ordinals, unique single-mail SHA headers, exact source inventory equality, source proofs for ancestry-only entries, bridge hashes and full normalized tree equivalence. Index-object preflight checked **11,318** distinct nonzero IDs. Four abbreviated historical IDs were unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. Full replay succeeded without their three-way fallback preimages.

The actual README Applying Bash block was extracted and executed unchanged, including its own verifier invocation, exit **0**. Its resulting commit `1ec7d96b6160c679b1b724310370e3accab01f25` separately passed `verify_mirror.compare` against the frozen source with this policy.

## Targeted tests and limits

The frozen source tree passed all ten changed Python test files with `scripts/run_tests.sh -j 1`, isolated HOME and `HERMES_TEST_FILE_RETRIES=0`: **199 passed, 0 failed**, exit **0**. The file inventory is listed in the feature document. The four exact-profile-home tests are included. This run does not repeat the implementation owner's broader 35-test or coach CLI RED/GREEN receipts; those are explicitly attributed in the feature doc.

On the frozen source, the changed TUI suite `src/__tests__/virtualHistoryOffsetCache.test.ts` passed **18 tests**, exit **0**, using Node 26.12.1 and Vitest 4.1.12. A first attempt could not collect because the workspace's `@hermes/ink` export bundle was absent after `npm ci --ignore-scripts`. Running `npm run build -w @hermes/ink` supplied that prerequisite; no tests or production code were changed. Vitest ran with one fork worker and a 1536 MiB heap cap.

The README-applied tree passed the same ten Python files: **199 passed, 0 failed**, with retries disabled and the same interpreter. Its independently installed lockfile workspace and built Ink bundle also passed the same TUI file: **18 passed**, exit **0**. No dependency directory was borrowed from the live runtime or shared between source and applied trees.

Staged `check_coverage.py`, documentation/JSON whitespace checks and the full pre-commit gate exited **0**. The private denylist found **zero hits across all 16 changed files**, including the bridge. The pre-commit gitleaks scan passed without new exceptions or bypasses. Previously published patches, bridges and verifier scripts have no changes.

No full-suite, native macOS/Windows, live Desktop GUI or deployed gateway behavioral proof is implied by mirror equivalence. The full coach suite remains reported red for independently tracked lifecycle and hardcoded-live-source cases; this mirror makes no all-green claim and performs no live core update.

## Independent review

An independent adversarial review found no blocking defects in the inventory, patch payloads, inherited normalization, bridge derivation, README slices or feature documentation. Its minor clock-monkeypatch wording and placeholder-base findings were corrected. Published patch bytes, checker code and comparison policy were not relaxed.
