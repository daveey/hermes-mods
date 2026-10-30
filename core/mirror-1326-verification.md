# Mirror 1326 verification

Executed October 30, 2026 PT. This pass appends 25 previously unpublished source commits as patches 1302–1326. Patch 1323 is the SSH-attached Desktop Stop-label and gesture change. Published patches 0001–1301 were not rewritten.

## Executed acceptance

```
python3 -B core/verify_mirror.py --source-repo <shared-source.git> --source-tip ff727b789d071d615d9225ba3c80c460580422af --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 --patch-dir core/patches --policy core/mirror-policy-1326.json
```

Exit 0, exact JSON output:

```json
{"base": "6bb377a51d5c8e279a7a5e34d717d653d421d064", "merge_base": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "patches": 1326, "source_tip": "ff727b789d071d615d9225ba3c80c460580422af"}
```

The verifier checks contiguous ordinals, unique source SHA headers, exact non-merge source/header set equality, complete `git am -3 --empty=keep` replay, and full-tree equality under the explicit policy. The upstream merge advanced the merge-base; the policy and current inventory record the new base.

Other acceptance checks: `python3 -B core/check_coverage.py`; denylist counts of zero for every appended patch and feature document; no `NNNN` placeholder in `core/turn-stop-notice.md`. Feature docs inventory the source tests, not newly executed runtime tests. No Kubernetes cluster, runner deployment, or GUI smoke test was performed by this mirror pass.

## Reviewed public normalization

The inherited substitutions, renames and curated fingerprints remain in effect. Two fingerprint entries differ from snapshot 1301:

- `.github/workflows/tfgar-probe.yml`: the infrastructure-specific probe target becomes a generic address in the same RFC1918 range. The public CGNAT range probe uses the shell expression `100.64.0.$((1))`, which resolves to the same generic CGNAT test address, avoiding a broad tailnet-address denylist match without dropping the isolation check. This is public example configuration, not a deployment target.
- `tests/hermes_cli/test_kanban_placement.py`: new source tests change the blob IDs; its reviewed divergence still consists solely of the existing product/profile fixture normalization plus standard substitutions. No new test assertion or implementation divergence is authorized.

The Kubernetes patch's mail description also drops an internal project name; it changes no source blob. All appended patches use the standard author and path scrub.

## Replay prerequisite and recovery evidence

The first replay stopped at historical patch 0690 because a required normalized ancestor blob was absent from the local source object store (`sha1 information is lacking`). Replaying the old snapshot from its original merge-base stopped at the same patch, establishing that the new patches were not the cause. A shared clone shares only the objects actually present on its source host: it does not guarantee the curated historical blobs exist there.

The hub source object store contained all 216 missing patch-index blobs. Only those objects were copied in a small pack into the disposable shared bare clone; no published patches were regenerated or changed. The subsequent full replay and tree comparison passed. Thus the acceptance gate caught the missing prerequisite before publication; successful source cloning alone had not checked it. For reproductions, supply the historical normalized index blobs as well as source commits and upstream history. Disposable repositories are removed after publication.
