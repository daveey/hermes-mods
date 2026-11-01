# Mirror snapshot 1478: executed verification

Frozen source: `a229655398887f15a65ab2a838f243611462a65d`. Upstream exclusion and current merge-base: `a4994d4ead8f9c915b485cec5689909b349a49df`.

## Full verification

Executed unchanged verifier, exit **0**:

```sh
python3 -B core/verify_mirror.py --source-repo /path/to/shared-src.git \
  --source-tip a229655398887f15a65ab2a838f243611462a65d \
  --base a4994d4ead8f9c915b485cec5689909b349a49df \
  --patch-dir core/patches --policy core/mirror-policy-1478.json \
  --replay-plan core/mirror-replay-1478.json
```

Exact output:

```json
{"base": "a4994d4ead8f9c915b485cec5689909b349a49df", "bridges": 4, "merge_base": "a4994d4ead8f9c915b485cec5689909b349a49df", "patches": 1478, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "a229655398887f15a65ab2a838f243611462a65d"}
```

This checks contiguous unique headers, the exact non-merge source set, complete replay and full normalized final tree. Published patches 0001–1476 are untouched. The policy changes only frozen source/base fields; no new normalization exemption was added.

The fourth hash-pinned bridge is the normalized tree diff from `fb8ae4e4361c1221695d7047360c663806719f20` through upstream merge `7dc18193ddb775a56bd1c1d1ac8ab1a8aec7d5ef`, inserted before 1478. It consolidates upstream merges between those two new fork commits while retaining all earlier published bytes. Both new patches and the bridge passed the private denylist scan (15 patterns, zero hits).

Index preflight examined 9,431 unique nonzero index IDs. Historical abbreviated object `9ff9444f10` was unavailable; complete replay nonetheless succeeded without requiring that preimage. No published index or verifier was changed to accommodate it.

## Feature verification

- [Classic title synchronization](classic-title-test.md): both live Linux CLI variants executed against the frozen source: 2 passed, 0 failed, 40.0 seconds.
- [Link fingerprint scope](links-fingerprint-scope.md): implementation RED/GREEN and exact-head CI evidence documented there.

Mirror equivalence does not establish live gateway CPU reduction. Deployment/native sampling remain a separate acceptance gate.
