# Mirror 1625 verification

Frozen source: `93402fd50df7dc43fe11d839564bdeb462eb0ba7`. Upstream exclusion:
`c7da41dd522dc19cc08bbfcfa69d4b078224b45d`. This pass appends **34** previously unpublished
source commits as **1592–1625**. All 0001–1591 patch bytes and ordinals are unchanged.
Commits landing after the frozen tip belong to the next pass.

The range was recomputed right before copying (`rev-list --reverse --no-merges upstream/main..origin/main`
minus published SHA headers). It held the 31 commits named when the pass was filed (`f1c99623b2` through
`794ec3a2fc`) plus three newer commits (`3cc6ce09c84`, `5eb82ae17d3`, `93402fd50df`), appended in rev-list order.
The SHA headers of 1592–1625 equal that list exactly. The source range `744ac50846..93402fd50df` contains no
merge commit, and the merge-base with `upstream/main` is still `c7da41dd522d`, so no new bridge is needed.

## Executed acceptance

The unchanged verifier exited **0** after replaying the entire series and comparing the full
resulting tree against the frozen source under `mirror-policy-1625.json`:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 93402fd50df7dc43fe11d839564bdeb462eb0ba7 \
  --base c7da41dd522dc19cc08bbfcfa69d4b078224b45d \
  --patch-dir core/patches --policy core/mirror-policy-1625.json \
  --replay-plan core/mirror-replay-1625.json
```

```json
{"base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "bridges": 6, "merge_base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "patches": 1625, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "93402fd50df7dc43fe11d839564bdeb462eb0ba7"}
```

`core/check_coverage.py` also exited 0. The source repo was a shared bare clone of the
fork's live checkout. Every nonzero preimage `index` ID in the 34 new patches was present in it.
No verifier logic was changed.

## Replay adaptations

`mirror-replay-1625.json` is a copy of its 1591 version with only `source_tip` changed (six bridges,
same replay start).

`mirror-policy-1625.json` is a copy of its 1591 version with `source_tip` changed and two existing
fingerprints re-pinned, because a new patch edited a file that already carried a reviewed normalization:

| Path | Changed by | Old pair (source / applied) | New pair (source / applied) |
|---|---|---|---|
| `tui_gateway/kanban_unloaded.py` | 1597 | `3b123ac96bc7` / `0e854dc52471` | `bf55c7217561` / `250d40d8a6f1` |
| `tui_gateway/update_pause.py` | 1608 | `dee7fb8b26e2` / `365cc944c942` | `c14965568aea` / `e670abe6adfd` |

For each, the diff between the new source and applied blobs was checked and has the same changed lines as
the diff between the old pair (only the hunk line offset differs, for `update_pause.py`): one comment line whose private chat id or host name is replaced by its public
placeholder. No other path differs. No fingerprint was added and no rename changed.

## Privacy and policy review

All new patches use the public author address and the standard home-path/project
substitutions. The private denylist scan found zero hits in every new patch and in every new or
changed doc. No card ids appear in the new or changed docs.

## Feature coverage

Every appended ordinal has a README row and a feature doc (README § "Mirror follow-ups 1592–1625").
New docs: [kanban-quota-episodes.md](kanban-quota-episodes.md), [test-stability-1592-1625.md](test-stability-1592-1625.md),
[desktop-plugin-loader-refresh.md](desktop-plugin-loader-refresh.md), [kanban-edit-priority-reason.md](kanban-edit-priority-reason.md),
[kanban-recovery-park-identity.md](kanban-recovery-park-identity.md), [background-shell-bash.md](background-shell-bash.md),
[local-session-policy-quarantine.md](local-session-policy-quarantine.md). Extended docs:
[kanban-worker-fence-refusal.md](kanban-worker-fence-refusal.md), [kanban-notice-refusal-rewind.md](kanban-notice-refusal-rewind.md),
[kanban-pr-park-handoff.md](kanban-pr-park-handoff.md), [kanban-creator-chat-routing.md](kanban-creator-chat-routing.md),
[kanban-origin-work-ledger.md](kanban-origin-work-ledger.md), [update-pause-followups.md](update-pause-followups.md),
[kanban-central-sync-client.md](kanban-central-sync-client.md), [provider-fallback-and-token-routing.md](provider-fallback-and-token-routing.md).
Each doc names the commits' own tests and lists untested paths. Those tests were not rerun by this mirror pass.
Runtime and live acceptance stay with the source owners.
