# Mirror 1474 verification

## Frozen scope

Appended **1470–1474**, five missing non-merge commits through `e572a3832043fdfc39bd5e43685e666852f94989`. Patch 1474 carries PR #487's composable metadata-only classification. Published patches 0001–1469 retain their exact bytes and ordinals. No plugin files changed in this mirror.

## Executed full-series check

The unchanged verifier exited **0**:

```sh
python3 -B core/verify_mirror.py \
  --source-repo ../src.git \
  --source-tip e572a3832043fdfc39bd5e43685e666852f94989 \
  --base 016b76da3a8a88e28775e08b6ca2846f471b8eda \
  --patch-dir core/patches \
  --policy core/mirror-policy-1474.json \
  --replay-plan core/mirror-replay-1474.json
```

Actual JSON stdout:

```json
{"base": "016b76da3a8a88e28775e08b6ca2846f471b8eda", "bridges": 3, "merge_base": "c419cf7c864ac0ee511dbd9f7091a8bba0b567ee", "patches": 1474, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "e572a3832043fdfc39bd5e43685e666852f94989"}
```

This checks contiguous ordinals, unique headers, exact source-set coverage, complete bridge-aware `git am -3 --empty=keep` replay and the full normalized final tree. The three inherited bridge files and verifier are unchanged.

Preflight checked 8,496 unique nonzero historical patch-index IDs; the same obsolete preimage `9ff9444f10` remains unavailable. Complete replay nevertheless succeeds by direct hunk application. This does not assert that every historical index object exists.

## Normalization review and privacy

All five appended patches passed a whole-file private-denylist scan. Author headers use the public noreply identity. Two new snapshot-bound fingerprint pairs account exclusively for anonymized investigation identifiers in the sync test module docstring and notice test comments, plus an anonymized laptop name in the latter. Their complete residual blob diffs were inspected: no executable line or assertion differs. Existing substitutions and fingerprint entries are retained unchanged. No acceptance logic was weakened.

Staged commit hooks additionally enforce gitleaks, privacy and documentation coverage. Source tests were inspected, not rerun here; implementation-owner RED/GREEN reports are explicitly attributed in the feature document. This mirror establishes neither plugin integration nor live deployment behavior.
