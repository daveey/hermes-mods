# Mirror 1476 verification

## Frozen scope

Appended **1475–1476**, the two missing non-merge source commits through `5a499bea1cf0b92834466c17673027e93607742d` (PR #486). All previously published patches retain their bytes and ordinals. Feature documentation: [State-keyed profile session scans](profiles-session-scan-cache.md).

## Executed full-series check

The unchanged verifier exited **0**:

```sh
python3 -B core/verify_mirror.py \
  --source-repo ../src.git \
  --source-tip 5a499bea1cf0b92834466c17673027e93607742d \
  --base 016b76da3a8a88e28775e08b6ca2846f471b8eda \
  --patch-dir core/patches \
  --policy core/mirror-policy-1476.json \
  --replay-plan core/mirror-replay-1476.json
```

Actual JSON stdout:

```json
{"base": "016b76da3a8a88e28775e08b6ca2846f471b8eda", "bridges": 3, "merge_base": "c419cf7c864ac0ee511dbd9f7091a8bba0b567ee", "patches": 1476, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "5a499bea1cf0b92834466c17673027e93607742d"}
```

This verifies contiguous ordinals, unique headers, exact non-merge source coverage, full three-bridge replay and the normalized final tree. Policy and replay-plan content is inherited unchanged from snapshot 1474 except for the frozen source tip; no new fingerprints or normalization exemptions were added. Author mail headers use the public noreply identity.

Source tests were exercised during implementation, not rerun by the mirror replay. Mirror verification is not production CPU acceptance.
