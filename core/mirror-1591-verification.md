# Mirror 1591 verification

Frozen source: `744ac50846c697afffec146710365547fba99cf7`. Upstream exclusion:
`c7da41dd522dc19cc08bbfcfa69d4b078224b45d`. This pass appends **3** previously unpublished
source commits as **1589–1591**. All 0001–1588 patch bytes and ordinals are unchanged.
Commits landing after the frozen tip belong to the next pass.

The range was recomputed right before copying (`rev-list --reverse --no-merges upstream/main..origin/main`
minus published SHA headers) and matched the card's three commits exactly. The source range
`02b2c78f28..744ac50846` contains no merge commit, and the merge-base with `upstream/main` is still
`c7da41dd522d`.

## Executed acceptance

The unchanged verifier exited **0** after replaying the entire series and comparing the full
resulting tree against the frozen source under `mirror-policy-1591.json`:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 744ac50846c697afffec146710365547fba99cf7 \
  --base c7da41dd522dc19cc08bbfcfa69d4b078224b45d \
  --patch-dir core/patches --policy core/mirror-policy-1591.json \
  --replay-plan core/mirror-replay-1591.json
```

```json
{"base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "bridges": 6, "merge_base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "patches": 1591, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "744ac50846c697afffec146710365547fba99cf7"}
```

`core/check_coverage.py` also exited 0. The source repo was a shared bare clone of the
fork's live checkout. Every nonzero preimage `index` ID in the three new patches was present in it.
No verifier logic was changed.

## Replay adaptations

None. `mirror-policy-1591.json` and `mirror-replay-1591.json` are copies of their 1588
versions with only `source_tip` changed. The new patches touch no fingerprinted or renamed path,
so no fingerprint was added or re-pinned, and the six bridges and the replay start are unchanged.

## Privacy and policy review

All new patches use the public author address and the standard home-path/project
substitutions. The private denylist scan found zero hits in every new patch and in every new or
changed doc.

## Feature coverage

Every appended ordinal has a README row and a feature doc (README § "Mirror follow-ups 1589–1591",
[kanban-restart-resume.md](kanban-restart-resume.md)). The doc names the commits' own tests and lists
untested paths. Those tests were not rerun by this mirror pass. Runtime and live acceptance stay with the
source owners.
