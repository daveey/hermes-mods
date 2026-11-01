# Mirror snapshot 1484: executed verification

Frozen source: `c21fe3290319da26cf0ebfa563e53aad7ff0ac2c` (merge of PR #493). Upstream exclusion and current merge-base: `a4994d4ead8f9c915b485cec5689909b349a49df` (unchanged since 1478).

## Full verification

The unchanged verifier ran on the hub (`nice -n 10`, about 55 s) against a shared bare clone of the hub's fork checkout. It exited **0**:

```sh
python3 -B core/verify_mirror.py --source-repo /path/to/shared-src.git \
  --source-tip c21fe3290319da26cf0ebfa563e53aad7ff0ac2c \
  --base a4994d4ead8f9c915b485cec5689909b349a49df \
  --patch-dir core/patches --policy core/mirror-policy-1484.json \
  --replay-plan core/mirror-replay-1484.json
```

Exact output:

```json
{"base": "a4994d4ead8f9c915b485cec5689909b349a49df", "bridges": 4, "merge_base": "a4994d4ead8f9c915b485cec5689909b349a49df", "patches": 1484, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "c21fe3290319da26cf0ebfa563e53aad7ff0ac2c"}
```

The run checked four things: contiguous unique headers, the exact non-merge source set (`rev-list --no-merges a4994d4e..c21fe329` equals the published headers), a complete replay through the four existing bridges, and the full normalized final tree. Published patches 0001–1478 are untouched. The six new patches, 1479–1484, were appended in `git rev-list --reverse --no-merges` order: the five PR #493 branch commits and PR #494's `c11f90d244`, which landed on main between them.

## Policy and replay changes (reviewed)

- `mirror-replay-1484.json` changes only `source_tip`. Bridges, start and hashes are unchanged.
- `mirror-policy-1484.json` changes `source_tip` and one existing fingerprint. `tests/hermes_cli/test_kanban_sync_idle_round.py` moved from `[5ee0115d…, 26431259…]` to `[83ba1ad4…, 0616b51c…]`, because patch 1483 adds tests to that file. In both pairs, the source/public diff is the same single docstring line: an internal investigation card id replaced by `example-investigation`. No new path, rename or substitution was added.

The first run, with only `source_tip` changed, failed on exactly that file (`Unexplained blob divergence: 83ba1ad4… / 0616b51c…`). The diff was reviewed before the fingerprint was moved.

## Privacy and prerequisites

- The new patches were scrubbed with the standard substitutions and `From:` normalization. The private denylist scan (15 patterns) shows zero hits in each of 1479–1484 and in the new docs.
- Index preflight: all 51 unique nonzero index IDs named by the new patches resolve in the source object store.

## Feature evidence

- [Human asks that resolve outside Hermes](kanban-ask-resolved-check.md) and [production board guard](kanban-production-board-guard.md): exact PR head `b41abac0746f0506a62bfcc8baf88da2a6bee2d8`, CI run `41294368192` succeeded. The 1484 import/schema fixes have no dedicated regression test (stated in the doc).
- [Sync repair revision](kanban-sync-repair-revision.md): exact PR head `c11f90d244d480a7fc42cedc28609e20b7226d0f`, CI run `41294239072` succeeded.

The mirror pass did not rerun the feature tests. Mirror equivalence makes no claim about live deployment.
