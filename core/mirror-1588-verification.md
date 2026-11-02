# Mirror 1588 verification

Frozen source: `02b2c78f2881a92c1c68cf423fc8780c9ff8682e`. Upstream exclusion:
`c7da41dd522dc19cc08bbfcfa69d4b078224b45d`. This pass appends **5** previously unpublished
source commits as **1584–1588**. All 0001–1583 patch bytes and ordinals are unchanged.
Commits landing after the frozen tip belong to the next pass.

The card was filed for three commits (1584–1586). Two more (`3cd523880e`, `02b2c78f28`) had
landed on `origin/main` by the time the range was recomputed. They are direct descendants of the
filed range with no new merge, so they joined this pass as 1587 and 1588. The source range
`ca4fa11665..02b2c78f28` contains no merge commit.

## Executed acceptance

The unchanged verifier exited **0** after replaying the entire series and comparing the full
resulting tree against the frozen source under `mirror-policy-1588.json`:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 02b2c78f2881a92c1c68cf423fc8780c9ff8682e \
  --base c7da41dd522dc19cc08bbfcfa69d4b078224b45d \
  --patch-dir core/patches --policy core/mirror-policy-1588.json \
  --replay-plan core/mirror-replay-1588.json
```

```json
{"base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "bridges": 6, "merge_base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "patches": 1588, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "02b2c78f2881a92c1c68cf423fc8780c9ff8682e"}
```

`core/check_coverage.py` also exited 0. The source repo was a shared bare clone of the
fork's live checkout. Every nonzero preimage `index` ID in the five new patches was present in it.
No verifier logic was changed.

## Replay adaptations

None. `mirror-policy-1588.json` and `mirror-replay-1588.json` are copies of their 1583
versions with only `source_tip` changed. The new patches touch no fingerprinted or renamed path,
so no fingerprint was added or re-pinned, and the six bridges and the replay start are unchanged.

## Privacy and policy review

All new patches use the public author address and the standard home-path/project
substitutions. The private denylist scan found zero hits in every new patch and in every new or
changed doc. Kanban task IDs and gateway labels in commit messages follow published precedent.

## Feature coverage

Every appended ordinal has a README row and a new or extended feature doc (README §
"Mirror follow-ups 1584–1588"). The docs name each commit's own tests. Those tests were not rerun
by this mirror pass. Test counts quoted in the docs come from the source commits. Runtime and
live acceptance stay with the source owners.
