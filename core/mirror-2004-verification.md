# Mirror 2004 verification

Frozen source tip: `0cbbc98ed684c4c5c1a561bcd42f335a53e152d5`.
Upstream exclusion tip: `84a0301415edd6d1385207ec96e4d7f2b6c1fcd9`.
Merge-base: `e26e0e4b67bfae31eddaaefee87c062efd67429b`.

Sixteen missing source SHA headers are appended as **1989–2004**, in reverse rev-list order. Previously published patches retain their exact bytes and ordinals. Neither verification script is modified.

## Replay and normalization

The 1988 policy and replay plan are copied with new independent source/base bindings. Eighteen historical bridges and three ancestry-only entries remain unchanged. Three new source integration bridges precede patches 1989, 1999 and 2001:

- `bridges/upstream-through-f95fb98c590.diff`: full first-parent diff of merge `f95fb98c5905f520ac71d1a09ae71dd895ac0bc3`.
- `bridges/upstream-through-5ff44110acc.diff`: full first-parent diff of merge `5ff44110acc7d21a900f9fe9b30bd1bab3525423`.
- `bridges/upstream-through-857ffb31e9b.diff`: cumulative diff from `78a282ac5de6a8fb093f458b75d45a1451e33a4e` through two consecutive integration merges ending at `857ffb31e9b2bfaf40c837b7ca6d6e410c808b65`.

Each bridge is normalized using the inherited literal/regex rules and hash-pinned in the replay plan. No final-tree replacement is used.

One existing fingerprint, `tui_gateway/update_pause.py`, is re-pinned after fork patch 2000 (`78a282ac5de`) changed that file. Its new source/applied blobs are `9d7681f7f3d1bf55e7f8de5c801a0d194368888f` / `e3962875224e8834b18ff676ae265982fc8cd368`. Their complete diff is the same previously reviewed hostname normalization to `example-laptop` in one comment. No code difference or new normalization exemption is accepted.

## Full verifier

This unchanged command exited **0**:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 0cbbc98ed684c4c5c1a561bcd42f335a53e152d5 \
  --base 84a0301415edd6d1385207ec96e4d7f2b6c1fcd9 \
  --patch-dir core/patches --policy core/mirror-policy-2004.json \
  --replay-plan core/mirror-replay-2004.json
```

```json
{"ancestry_only": 3, "base": "84a0301415edd6d1385207ec96e4d7f2b6c1fcd9", "bridges": 21, "merge_base": "e26e0e4b67bfae31eddaaefee87c062efd67429b", "patches": 2004, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "0cbbc98ed684c4c5c1a561bcd42f335a53e152d5"}
```

This checks contiguous ordinals, unique single-mail SHA headers, exact source inventory, ancestry-only source proofs, bridge hashes, full replay and normalized full-tree equivalence. Index-object preflight checked **11,481** distinct nonzero IDs; four historical abbreviated preimages remain unavailable (`43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`). Replay succeeds without needing those fallback preimages.

The actual README Applying Bash block was extracted and executed unchanged, including its verifier invocation: exit **0**. The resulting tree at `1196dc03ba4af075c271ff822cf58cacabac2d93` separately passed `verify_mirror.compare` against the frozen source with this policy.

## Executed tests and limits

All **20 changed Python test files** passed independently in the frozen source and README-applied trees using `scripts/run_tests.sh -j 1`, isolated HOME and `HERMES_TEST_FILE_RETRIES=0`: **410 passed, 0 failed in each tree**. The file set covers wake episodes, failed HTTP attempt timing, sync timing/analyzer, stop-gate drafts, process exit hints, persistent scopes, shutdown receipts and update-held submit queues. Feature docs list the individual tests; their implementation-owner receipts are distinguished from this mirror's execution.

Desktop TypeScript suites were **not rerun** by this mirror pass. No full-suite, native macOS/Windows, live GUI or deployed gateway behavioral proof is implied by source/tree equivalence.

## Privacy gates and independent review

The private denylist returned zero hits for every one of the 31 staged files, including all three new bridges. Staged coverage and documentation/JSON whitespace checks passed. The pre-commit gitleaks gate passed with twelve new exact file/rule/line fingerprints covering thirteen findings: synthetic upstream redaction/connector test fixtures and a public Mattermost channel-ID context example. No broad exemption or scan bypass was added.

Independent adversarial review reconstructed all sixteen patches and three bridges byte-for-byte, checked the new bridge hashes/boundaries, confirmed the single fingerprint re-pin preserves only its prior comment normalization, re-ran tree comparison and privacy gates, and checked the actual README recipe slices. It found no blocking defect. Its wording correction about the fork origin of the fingerprint change is incorporated above.

The timing diagnostics are off by default and bounded to 600 seconds. **Latency acceptance remains unproven**: no matched legacy baseline exists. Mirror publication does not enable diagnostics or accept the rollout.
