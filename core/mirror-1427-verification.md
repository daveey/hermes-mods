# Mirror 1427 verification

Frozen source tip: `1b20db1838e748ce13cbc974fcaa6681cff13da8`.
Upstream exclusion base: `6bb377a51d5c8e279a7a5e34d717d653d421d064`.

## Inventory and replay

Appended all 12 missing non-merge source commits as **1416–1427**. The complete
source-header set, contiguous ordinals and unique headers were checked by the
full verifier. Published patch bytes and ordinals are unchanged. The new patches
and documentation passed a private-denylist scan with zero matches per file.

The 1415 policy and bridge-aware replay plan were copied to 1427. Two existing
whole-file fingerprint entries needed new blob pairs because the placement
implementation and its tests changed:

- `hermes_cli/kanban_placement.py`: `88440745d57004e4990d9a862bdd3c71452474b8` / `e3f602875fe630ae8d817302ab932cd09c03b827`.
- `tests/hermes_cli/test_kanban_placement.py`: `e28dfd5baac440a8f382aa1ad85728621a5bd9c1` / `97bb07e3299150c253b65a5c315ac87213404624`.

For both paths, programmatic comparison of the old and new residual diffs after
standard substitutions returned identical: only inherited public fixture/name
normalizations remain. No new semantic difference was exempted. The initial
full verifier rejected the stale test-file fingerprint before publication;
coverage alone cannot detect that whole-file policy bookkeeping change. The
unchanged full verifier passed after re-pinning these reviewed pairs.

Patch-index preflight checked 8325 object IDs and found one unavailable historical
preimage, `9ff9444f10`. Full replay nevertheless succeeded without it: its hunk
application did not require that three-way preimage. This does not claim that
all historical index objects are available in a fresh source clone.

## Executed acceptance

From the public repository worktree, with `SOURCE_REPO` pointing to a shared bare
clone containing the frozen source and exclusion commits:

```sh
python3 -B core/verify_mirror.py \
  --source-repo "$SOURCE_REPO" \
  --source-tip 1b20db1838e748ce13cbc974fcaa6681cff13da8 \
  --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 \
  --patch-dir core/patches \
  --policy core/mirror-policy-1427.json \
  --replay-plan core/mirror-replay-1427.json
```

Exit **0**, full output:

```json
{"base": "6bb377a51d5c8e279a7a5e34d717d653d421d064", "bridges": 1, "merge_base": "ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8", "patches": 1427, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "1b20db1838e748ce13cbc974fcaa6681cff13da8"}
```

This verifies the full inventory, bridge-aware replay, and final normalized-tree
equivalence, not only application of the appended range.

## Limits

Source feature tests listed in the feature documents were not rerun during this
publication pass. Real Mac sleep/wake acceptance remains separate; no live sleep
proof, new deployment result, or source-suite pass count is claimed here.
