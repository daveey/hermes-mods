# Mirror snapshot 1494: executed verification

Frozen source: `92b72abf17e34e490a1b37419e1f3b7044cfa925` (PR #500 merge). Upstream exclusion: `a4994d4ead8f9c915b485cec5689909b349a49df`.

The unchanged verifier ran against a task-owned shared bare source clone and exited **0**:

```sh
python3 -B core/verify_mirror.py --source-repo /path/to/shared-src.git \
  --source-tip 92b72abf17e34e490a1b37419e1f3b7044cfa925 \
  --base a4994d4ead8f9c915b485cec5689909b349a49df \
  --patch-dir core/patches --policy core/mirror-policy-1494.json \
  --replay-plan core/mirror-replay-1494.json
```

Exact output:

```json
{"base": "a4994d4ead8f9c915b485cec5689909b349a49df", "bridges": 4, "merge_base": "a4994d4ead8f9c915b485cec5689909b349a49df", "patches": 1494, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "92b72abf17e34e490a1b37419e1f3b7044cfa925"}
```

This verifies contiguous ordinals, unique SHA headers, exact source coverage, full four-bridge replay and normalized final-tree equality. Published patches 0001–1492 are unchanged. Policy and replay files differ from snapshot 1492 only in the frozen source tip; no fingerprints, bridge hashes or exceptions were added.

Feature execution evidence is listed in [board-targeted-sync.md](board-targeted-sync.md): 310 local tests, 15 checks and the exact-head required CI aggregate. Mirror publication does not establish live CPU acceptance.
