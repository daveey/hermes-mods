# Mirror snapshot 1495: executed verification

Frozen source: `e745574d5026db1d31e36d200d7b48e440c8d8da` (PR #502 merge).

The unchanged verifier exited 0 against a shared bare source clone:

```sh
python3 -B core/verify_mirror.py --source-repo /path/to/shared-src.git \
  --source-tip e745574d5026db1d31e36d200d7b48e440c8d8da \
  --base a4994d4ead8f9c915b485cec5689909b349a49df \
  --patch-dir core/patches --policy core/mirror-policy-1495.json \
  --replay-plan core/mirror-replay-1495.json
```

Exact output:

```json
{"base": "a4994d4ead8f9c915b485cec5689909b349a49df", "bridges": 4, "merge_base": "a4994d4ead8f9c915b485cec5689909b349a49df", "patches": 1495, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "e745574d5026db1d31e36d200d7b48e440c8d8da"}
```

This checks exact inventory, unique headers, contiguous ordinals, full four-bridge replay and normalized tree equality. Published patches through 1494 are unchanged. Policy and replay differ from snapshot 1494 only in source tip; no new exceptions or fingerprints.

Feature tests and exact-head CI: [board-targeted-sync.md](board-targeted-sync.md). Live CPU acceptance is separate and not established by this mirror.
