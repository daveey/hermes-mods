# Mirror 1415 verification

Frozen source tip: `160518392aaf58cec3b8ac04d9712e635b05fdf5`.
Upstream exclusion base: `6bb377a51d5c8e279a7a5e34d717d653d421d064`.

## Inventory and replay

Fetched both source remotes and the public remote before discovery and again before copying; 34 non-merge commits
had no published SHA header and were appended as 1382–1415 in `git rev-list --reverse --no-merges` order. Published
patch bytes and ordinals are unchanged. Each new patch has the public author header and the standard path/app-name
substitutions, and was scanned against the private denylist (0 matches for every file).

The 1381 policy and replay plan were copied to 1415 with `source_tip` changed. The first run failed with
`Unexplained blob divergence` on two paths, both files that already carried a reviewed historical normalization
(older mirror scrubs rendered product and org names differently from the current substitutions):
`hermes_cli/kanban_placement.py` (patch 1408 changed its blob) and `tests/hermes_cli/test_kanban_placement.py`
(patch 1398). The diff of each, source blob with policy substitutions against the replayed blob, contained only the same
historical name normalizations ("Baby Bear" -> "Example App", an org name), none from the new patches. The two
fingerprint pairs were therefore re-pinned to the new blob IDs (`84bcc128…` -> `e9881d39…`; `73857645…` ->
`3464e739…`); no other policy entry changed and no verifier rule was weakened.

```sh
python3 -B core/verify_mirror.py \
  --source-repo "$PWD/src.git" \
  --source-tip 160518392aaf58cec3b8ac04d9712e635b05fdf5 \
  --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 \
  --patch-dir core/patches \
  --policy core/mirror-policy-1415.json \
  --replay-plan core/mirror-replay-1415.json
```

Exit **0**, exact JSON:

```json
{"base": "6bb377a51d5c8e279a7a5e34d717d653d421d064", "bridges": 1, "merge_base": "ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8", "patches": 1415, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "160518392aaf58cec3b8ac04d9712e635b05fdf5"}
```

This checks contiguous ordinals, unique SHA headers, the complete source-header set, the bridge-aware full replay and
final tree equivalence under the public normalization policy.

## Not run

The source tests named in each feature doc were not rerun for this pass; they are source-repo tests carried in the
patches, not tests of the normalized public replay.
