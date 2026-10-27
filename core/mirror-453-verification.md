# Public mirror: 453-patch series (0402–0453 appended)

Patches 0402–0453 continue the merge-adapted series after 0401 (`5cd9bd0161`, kanban sync seams). They are the 52
commits that the earlier mirror skipped, in current-history order (`git log --reverse --no-merges
0146a882ed..origin/main`, minus `5cd9bd0161` which is already 0401), cherry-picked with `-x` onto a worktree that had
the 401 existing patches applied. Each patch's trailer names its source commit; 0452 is source `eb215058a7`
(a pick of `8ae2a621a9`).

- Existing patches 0001–0401 are byte-unchanged.
- Three conflicts were resolved by keeping the already merge-adapted side (fixtures in `test_projects_rpc.py` from
  0393); `test_kanban_pr_continuation.py` was resolved to the source version in the last patch, 0453.
- A fresh detached worktree at `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63` ran
  `git am -3 --empty=keep core/patches/*.patch` (exit 0, 453 patches). The resulting tree differs from the source
  tree only in files that already differed before this series (public scrub and cosmetic 0393 merge adaptations).
- The private denylist scan was clean for every new patch and doc; `python3 -B core/check_coverage.py` passes.
- Source commits that landed after `eb215058a7` are not in this range.
