# Mirror 1368 verification (NOT PASSING)

Appends 42 previously unpublished source commits as patches 1327–1368 (source tip `632eb9c39d518188e71d3404c5f012d4e4e6a113`, upstream exclusion tip `6bb377a51d5c8e279a7a5e34d717d653d421d064`). Published patches 0001–1326 were not rewritten. Patch headers and feature docs for 1327–1368 are complete and denylist-clean (`:0` per file).

## Executed acceptance: FAILED

```
python3 -B core/verify_mirror.py --source-repo <shared-source.git> --source-tip 632eb9c39d518188e71d3404c5f012d4e4e6a113 --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 --patch-dir core/patches --policy core/mirror-policy-1368.json
```

Exit 1 (`Mirror verification FAILED`). Inventory (contiguous ordinals, header set) passed; the `git am -3 --empty=keep` replay stops at patch 0014 with i18n conflicts.

Cause: the fork merged upstream's error-card v2 work (merge `00496888ef3` / `3234d338163`), so `git merge-base base tip` moved from `831637fe8d5` to `ef35e8b9e1a`. That upstream change rewrites 41 desktop files the early patches also touch, so the published series no longer replays at the new merge-base. Replaying at the old merge-base cannot work either: 1328 (`a994fd14406`) and later assume the merged tree. The policy file `mirror-policy-1368.json` is the 1326 policy with the tip updated and has not been reviewed against a passing tree comparison.

This snapshot is therefore not a verified inventory and is not on `main`. Needed: a decision on how to re-baseline the mirror (re-linearize against the new merge-base as in the 1134 pass, or teach the verifier a bridge for an upstream merge).
