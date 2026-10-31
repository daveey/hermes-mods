# Mirror 1368 verification: PASS

## Frozen inventory and preserved publication

1368 contiguous, uniquely identified non-merge source commits, through fork tip `632eb9c39d518188e71d3404c5f012d4e4e6a113`; upstream exclusion tip `6bb377a51d5c8e279a7a5e34d717d653d421d064`. This includes managed-worker log headers (`f06cd838bbc`) and detached-fork pre_verify behavior (`966154e8d35`). Patches 1327–1368 and their feature documents were integrated from the prepared mirror branch. All 1368 existing patch files retain their bytes, SHA headers, filenames and ordinals: this recovery does not re-linearize or silently rewrite publication history.

The current merge-base is `ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8`. Replay uses the explicit historical start `831637fe8d58f050e9aa776b621bb2c7b614dcd1`, not the current merge-base.

## Explicit upstream-merge bridge

`mirror-replay-1368.json` binds the frozen tip, exclusion base, historical start, boundary source SHA and SHA-256 of `bridges/error-card-v2.diff`. Apply patches 0001–1327, apply and commit that bridge, then apply patches 1328–1368. The README's current Applying section gives the consumer recipe. A single all-patches glob at the current merge-base is not supported.

The bridge is byte-identical to `git diff --binary --full-index 00496888ef3^1 00496888ef3`, including its fork-side merge resolutions across 41 paths. Its SHA-256 is `e2e97e2348a1b6cdaef8b8c969beeefa29a8412c2a0b5ac6c37383773f7fe9ad`. Merge `00496888ef3` integrates upstream `ef35e8b9e1a`; it is an ancestor of the boundary commit `a994fd14406` (patch 1328). No other separate bridge is needed for this frozen snapshot: the complete final-tree comparison passes with exactly this one bridge. This does not certify every intermediate tree against its source commit.

## Executed acceptance

Executed against a shared bare clone of the existing fork checkout (no live source HEAD changes):

```sh
python3 -B core/verify_mirror.py \
  --source-repo /path/to/shared-source.git \
  --source-tip 632eb9c39d518188e71d3404c5f012d4e4e6a113 \
  --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 \
  --patch-dir core/patches \
  --policy core/mirror-policy-1368.json \
  --replay-plan core/mirror-replay-1368.json
```

Exit **0**, with this exact JSON:

```json
{"base": "6bb377a51d5c8e279a7a5e34d717d653d421d064", "bridges": 1, "merge_base": "ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8", "patches": 1368, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "632eb9c39d518188e71d3404c5f012d4e4e6a113"}
```

The verifier checks the exact non-merge source-header set, contiguous unique ordinals, full ordered replay and complete final tree (paths, modes, kinds and blob contents) under the explicit normalization policy. Every source patch still replays once. No comparison or inventory check was disabled.

Preflight examined 8083 distinct nonzero historical patch-index IDs: 8082 resolved; obsolete preimage `9ff9444f10` in patch 0402 did not. The complete replay succeeds without that object (its hunk applies directly); no missing-object workaround or patch rewrite was required. This is not a claim that every historical index ID is available. Both source snapshot commit objects were present.

Policy review found one changed fingerprint pair, for `tests/hermes_cli/test_kanban_placement.py`: source `942b745862d18c301a94fb71e665132f415ea828`, applied `a825a89761d178b94d9dd2e0586e4f51a0442d17`. The complete residual diff contains only inherited example product-name and example organization-profile substitutions in fixture titles/comments. No assertions were removed or newly normalized here. The remaining policy entries are inherited and are required to match exactly (unused fingerprints fail verification). These normalized fixture semantics were not freshly runtime-tested by this mirror recovery.

## Executed tests and privacy checks

- `python3 -B core/test_verify_mirror.py`: **25 tests passed**. New cases exercise bridge replay through both Python API and CLI; snapshot, boundary, hash, non-merge, unpinned-ref, ancestry and path-traversal rejection; and final-tree rejection even when a modified bridge has a matching hash. Existing inventory, normalization, cleanup and fail-closed tests remain passing.
- `python3 -B core/check_coverage.py`: exit **0**.
- Bridge diff private-denylist scan: **0 matches**. Prepared patches were already scrubbed; normal public pre-commit secret/privacy/coverage checks are required without bypass.
- Independent review replayed the complete snapshot successfully and verified the bridge against the actual source merge. Review's current-consumer-documentation and bridge-privacy findings are addressed here.

No full core runtime suite, Desktop rendering test, deployment or exact-head source CI rerun is claimed. This recovery verifies the public mirror, not new runtime behavior.

## Failure analysis and prevention

Problem: the prepared 1368 snapshot's inventory passed but replay at the new merge-base failed at patch 0014 (i18n conflicts).

Why 1: upstream's error-card-v2 merge changed the early patches' i18n context. Evidence: source merge `00496888ef3`, its first-parent 41-path diff, and the moved merge-base.

Why 2: replaying all historical patches at a single newly computed merge-base assumes those curated patches can be transplanted across an upstream merge; replaying only at the old base omits merge content that patch 1328 expects. Evidence: the previous verifier's unconditional merge-base start and the old-base + bridge + tail replay that now passes.

Root (happened): the linear mirror format represented non-merge source commits but had no explicit replay schedule for later upstream-merge boundaries. Countermeasure: an opt-in, snapshot-bound replay plan that preserves published patch bytes and inserts a separately reviewed merge bridge.

Why not caught earlier: the lightweight coverage gate checks documentation and duplicate headers, not replay context or full-tree equivalence. The mandatory full verifier did catch this before publication; its failure was preserved on the WIP branch. The older 1134 recovery documented the same class of upstream-context failure, but solved it with a rewritten curated series rather than a reusable explicit boundary mechanism.

Detection: bridge API/CLI regression fixtures and tampered-bridge final-tree rejection, plus the unchanged mandatory complete-series verifier. A passing apply alone remains insufficient.

## Snapshot boundary

Recomputed unpublished source headers after fetching origin: `970d5e40c12d5d33bf7c90faeb4d414057994e54` and `12d643cf97a685750a366c4a3e5c9319a39c6c7b` were beyond this frozen snapshot. They are not silently included or claimed verified here; the parent mirror owner receives their handoff.
