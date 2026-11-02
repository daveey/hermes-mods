# Mirror 1583 verification

Frozen source: `ca4fa11665dc749496f76239a631e7d9c3eeca09`. Upstream exclusion:
`c7da41dd522dc19cc08bbfcfa69d4b078224b45d`. This pass appends **44** previously unpublished
source commits as **1540–1583**. All 0001–1539 patch bytes and ordinals are unchanged.
Commits landing after the frozen tip belong to the next pass.

The tip was first frozen at `673f6c936b75` (1540–1582). One more commit (`ca4fa11665`, the
boot-time project tree prewarm) landed during the pass. It was a direct child of that tip
with no new merge, so the freeze moved to it and the commit became 1583. The verifier below
ran against the final freeze.

## Executed acceptance

The unchanged verifier exited **0** after replaying the entire series and comparing the full
resulting tree against the frozen source under `mirror-policy-1583.json`:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip ca4fa11665dc749496f76239a631e7d9c3eeca09 \
  --base c7da41dd522dc19cc08bbfcfa69d4b078224b45d \
  --patch-dir core/patches --policy core/mirror-policy-1583.json \
  --replay-plan core/mirror-replay-1583.json
```

```json
{"base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "bridges": 6, "merge_base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "patches": 1583, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "ca4fa11665dc749496f76239a631e7d9c3eeca09"}
```

`core/check_coverage.py` also exited 0. The source repo was a shared bare clone of the
fork's live checkout. No verifier logic was changed.

## Replay adaptations

- **Sixth bridge.** The fork merged upstream tip `c7da41dd` through `735ec9ef761`, which
  was then merged into main as `6e2fbc15549`. Non-merge inventory does not carry that upstream
  content. `bridges/upstream-6e2fbc15549.diff` is the exact first-parent diff of
  `6e2fbc15549` (`a7f0ae1033d..6e2fbc15549`, plain `git diff`) with the standard three
  substitutions applied. It is hash-pinned (`f30ba7dab10b…`) before **1549** (`1a92241da8b`),
  the first patch after that merge in published order. The other five bridges and the replay
  start are unchanged.
- **Empty duplicate entry 1541.** Source commits `ac7f935cd60` (1540) and `c538e9b9723` (1541)
  have byte-identical diffs: two copies of one change, joined by merge `7e47f5692aa`. 1541 keeps
  its distinct SHA header and subject as an explicit empty mail entry, applied with
  `--empty=keep`. Its change is carried by 1540. No source commit or ordinal is skipped
  (this follows the 1516–1518 precedent).

## Privacy and policy review

All new patches use the public author address and the standard home-path/project
substitutions. The private denylist scan found zero hits in every new patch, in the new
bridge and in every new or changed doc. Kanban task IDs and gateway labels in commit messages
follow published precedent.

One fingerprint was re-pinned. No new fingerprint and no rename was added.
`tui_gateway/kanban_remote_notices.py` already carried a reviewed comment-identifier
normalization (a private card/chat identifier replaced by `t_example123` /
`20260102_120000_example`). Patch 1563 changed the source file, so the pinned source and
applied blobs changed. The only remaining difference after substitutions is that one
normalized comment line. Runtime logic is identical.

## Feature coverage

Every appended ordinal has a README row and a new or extended feature doc (README §
"Mirror follow-ups 1540–1583"). The docs name each commit's own tests. Unless they say
otherwise, those tests were not rerun by this mirror pass. Test counts and CI results quoted
in the docs come from the source owners. Runtime, Desktop and live-provider acceptance stay
with them.
