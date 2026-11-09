# Mirror 2065 verification

Frozen source: `134eca6553a85e6424e05373fd5b340ab49db811`; upstream exclusion tip: `496706271914b585f402bfd070b01070cf289553`.

## Inventory and replay

The unchanged `verify_mirror.py` exited **0** with this exact output:

```json
{"ancestry_only": 3, "base": "496706271914b585f402bfd070b01070cf289553", "bridges": 38, "merge_base": "0b82ddc96c39449d30763d0fdd50ac22fed0fb31", "patches": 2065, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "134eca6553a85e6424e05373fd5b340ab49db811"}
```

This checks contiguous ordinals, unique source SHA headers, exact non-merge source inventory, source-parent proofs for ancestry-only entries, and policy-normalized full-tree equality. All **2029 previously published mail patches and 27 bridges remain byte-unchanged**. The 36 new mails follow the missing-header `rev-list --reverse --no-merges` order.

The README Applying Bash block was independently extracted and executed verbatim, then its resulting tree was compared to the frozen tip with the unchanged `verify_mirror.compare` and current policy. Both application and tree comparison passed; clean application alone was not accepted as proof.

Index-object preflight found the same four unavailable historical abbreviated objects as the prior snapshot: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. Both complete replays succeeded without requiring those fallback preimages. They are not claimed present.

## Bridge provenance and independent review

Eleven new upstream-integration bridges are hash-pinned in [the replay plan](mirror-replay-2065.json). Each is the policy-normalized binary diff between the source revisions below. Each range introduces no non-merge fork commit outside the exclusion base; no published patch body was weakened to make replay pass.

| Before patch | Source diff | Merge anchor |
|---|---|---|
| 2033 | `e71ada4e5910b0a38e09066fe12458ea4678765d..6b6e1bebdb20ece5917e7a3f896009910d05879a` | `6b6e1bebdb20ece5917e7a3f896009910d05879a` |
| 2035 | `92c0b758149b868b98154fec174f9423721676d0..23e1667a3e39618932be0b3b26e1552cd74d97a9` | `23e1667a3e39618932be0b3b26e1552cd74d97a9` |
| 2040 | `25b72fadef22b090f5e25b6c96cb9a1a25f03e38..1d82a1057d77f7c685a6f13df1f52f51e4bf805f` | `1d82a1057d77f7c685a6f13df1f52f51e4bf805f` |
| 2041 | `0565c58a80f3595c15000ef02280ef32192c807c..f953096085bab946f2a460bf8c71461a0bd31e59` | `f953096085bab946f2a460bf8c71461a0bd31e59` |
| 2042 | `dee02b0403f6145ddc4a5d17a8bc9d7bd64971a1..c38e015a358cc743e1f3a61abaf4ef0df199630f` | `c38e015a358cc743e1f3a61abaf4ef0df199630f` |
| 2043 | `a0743b91101c46548f264328ea0983a274ecc54e..57af00824401e9c5498e1ebfe0bccfe58e9bc387` | `57af00824401e9c5498e1ebfe0bccfe58e9bc387` |
| 2044 | `fff879ec83b9784fd70f1f9887a5ce91081457eb..fc8c7cf0bcd73ff1851e2624e0aaa6c014414806` | `fc8c7cf0bcd73ff1851e2624e0aaa6c014414806` |
| 2050 | `fa9c993ce859cad81e6af589f2256c7b5f731065..f9b6debe0b1735b49c631235a8f0c8608cd1758e` | `f9b6debe0b1735b49c631235a8f0c8608cd1758e` |
| 2053 | `2f511746ea4f2293d5cdc6fdfc022e56f2972c5f..176cb9d46ae4d3b7b11c11091ff12ee532cd446b` | `176cb9d46ae4d3b7b11c11091ff12ee532cd446b` |
| 2055 | `408c0da446c594c74513b0def49e3e962659ff3a..d40419b43268839a4aa1f1b141249ba41e10b614` | `d40419b43268839a4aa1f1b141249ba41e10b614` |
| 2062 | `1774a20d38213497a8e755bd90949e00eae2179b..b7c02d5dec61f9fd8abbe6f6f0de4318f497f74f` | `b7c02d5dec61f9fd8abbe6f6f0de4318f497f74f` |

Bridge 2053 starts before the two newer mainline test/CI commits: the upstream delta touches none of their files. Its 516 added non-merge source commits are all upstream. Patch 2053 then edits the integrated runtime-loader; patch 2054 applies on its older source-parent context without undoing that integration. The upstream delta before 2055 is relative to the integration merge, so it does not duplicate patch 2053. Subsequent branch-interleaved patches retain their source diffs and apply once.

An independent reviewer regenerated all eleven bridges byte-for-byte, checked their pins and source anchors, reconstructed the final source tree by applying raw source bridge and patch diffs in an isolated index, and obtained exact tree equality. Review also checked merge-tree equality for the fourteen merges without their own bridge, README slice boundaries, and old-byte preservation.

Three existing normalization fingerprints were repinned for changed source files: `hermes_cli/kanban_overlap.py`, `tui_gateway/kanban_remote_notices.py`, and `tui_gateway/kanban_unloaded.py`. For each, the new source-to-applied diff has exactly the same added/removed lines as the previous reviewed pair. No normalization rule, rename, or fingerprint key was added or removed.

## Runtime tests and limits

The README-replayed public tree independently provisioned a fresh Python test environment using `pm.build_env --group dev --group test`. The canonical runner executed:

```sh
scripts/run_tests.sh -j 1 tests/agent/test_tool_result_summaries.py tests/tools/test_image_source_not_found.py
```

Result: **9 passed, 0 failed**, across two isolated test-file processes: six compaction-summary cases (including terminal and execute_code) and three local image-source cases. The actual source clips edges to **80 characters per line**, not the approximate 180-character brief. [The feature doc](compaction-output-stubs.md) records the exact thresholds and test gaps.

Other feature tests are documented from their patches but were **not rerun** in this mirror pass. No full suite, native macOS/Windows, JavaScript, runtime performance, production recovery, or source-vs-public runtime-test parity is claimed.

## Publication checks

Coverage and docs/JSON whitespace checks pass. The private denylist has no matches in new content. Gitleaks initially identified 18 findings at 17 distinct lines in upstream bridges: synthetic bearer/vault/connection/cache fixtures, removed SQLite API macros, and a public HKDF label. Independent source-history review confirmed none is a credential. Exact file/rule/line fingerprints in `.gitleaksignore` preserve the original public source bytes; there is no broad exclusion.

## Verification failures resolved before publication

- **Fingerprint check:** the first replay reached full-tree comparison but refused three changed source blobs. Why: prior fingerprint pairs bind whole blobs, and these files gained legitimate code while retaining the same historical public normalization. Root (happened): snapshot-bound evidence must advance with source changes, not be copied blindly. Root (not caught): it **was** caught by the unchanged verifier before publication. Proof: added/removed normalization lines match the prior pairs exactly, independent review agreed, and the repinned full replay exits 0. Recurrence: the core mirror skill already documents this re-pin case; its strict gate was retained.
- **Test provisioning:** initial PM setup mistook this scratch layout for a packaged payload. Why: the mirror's parent directory contained an inventory file named `manifest.json`; `pm/runtime.py::_resident_runtime` treats a parent file of that name as a package marker. Root (happened): a generic scratch artifact name collided with a runtime layout sentinel. Root (not caught): no parent-layout check preceded provisioning; PM correctly refused the incomplete payload. Countermeasure/proof: renamed the scratch inventory to `patch-manifest.json`, then unchanged PM provisioning and the nine tests passed. Recurrence: no earlier occurrence was identified in the loaded mirror procedure; that procedure now warns against the sentinel name.
