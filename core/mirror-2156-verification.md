# Mirror snapshot 2156 verification

## Scope

Appended 25 distinct source commits as ordinals 2132–2156, frozen at
`e5c02292e40506da22ab96b09215ca1c1d56ebb2`. The preceding snapshot ended at
`f953f662ccd84cbb643d71e113f9a13f939c4a94`. No merges occur between these tips.
The upstream exclusion remains `8471f614b4879144d36e8919fb4e1f3c430d2100`;
fetching a newer upstream does not change this frozen inventory boundary.
All published patches and all 41 bridges are byte-preserved. No hotkeys changed.

## Executed acceptance

The unchanged `verify_mirror.py`, invoked by the README Applying recipe with
`mirror-policy-2156.json` and `mirror-replay-2156.json`, exited 0:

```json
{"ancestry_only": 3, "base": "8471f614b4879144d36e8919fb4e1f3c430d2100", "bridges": 41, "merge_base": "b3746e35ddd32079697728e2df57a9d78f1e9e32", "patches": 2156, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "e5c02292e40506da22ab96b09215ca1c1d56ebb2"}
```

This checks contiguous ordinals, unique single-message SHA headers, complete source-set equality,
all replay bridges, ancestry-only entries at their source parents, and final full-tree equivalence
under the explicit normalization policy. The README's actual Bash recipe was also executed end to end;
its resulting tree passed the same `compare` function independently.
`check_coverage.py` exited 0 with the new files staged. Documentation/JSON whitespace checks passed.
Private denylist scan of the new patches, policy, replay plan and feature docs found zero hits.

## Normalization and object prerequisites

Only three existing fingerprint pairs were re-pinned: `hermes_cli/kanban_placement.py`,
`tests/hermes_cli/test_kanban_placement.py`, and `tui_gateway/kanban_unloaded.py`.
For each pair, the ordered non-equal byte-line edit blocks between normalized source and public
blob were compared with the preceding policy's pair and were exactly identical. Thus these are
unchanged historical normalization deltas at new source revisions, not newly waived behavior.
No published policy was edited. The placement module's inherited malformed hostname-regex
scrub artifact remains documented in the courier identity feature page; this pass does not fix it.

Preflight checked 12,202 distinct nonzero patch-index object identifiers. Four historical
abbreviations were initially unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`,
`a3701890ce1`. Replay applied their hunks without needing those preimages; no object substitution,
patch rewriting or verifier weakening was required. A different object store must still satisfy
any three-way fallback it actually needs.

## Limits

This is mirror/inventory/replay verification, not a new runtime release. The source feature tests
listed in the linked documentation were inspected but were not executed by this mirror pass.
No Desktop live flow, full product suite, performance remediation, or host rollout is claimed.
Later source commits remain outside this frozen snapshot.
