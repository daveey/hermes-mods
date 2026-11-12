# Mirror snapshot 2491: inventory and whole-tree proof

## Scope

Append **2410–2491** (82 non-merge source commits) to the immutable 2409 series.
Frozen source: `6d9af3bb7d7d3da1053792951d5e9ba1025cf90c`.
Frozen upstream exclusion: `e3bf4be5df04cf72dab4914b073361c71af1dbe1` (unchanged).
Merge-base: `78abaafcccec20a546c94e8734a70e38c08a6a69` (unchanged).
Published ordinals, old mail bytes, all 44 bridges, the three ancestry-only entries, all historical
policies and `verify_mirror.py` are unchanged. The replay still starts at the historical pinned start
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range `10a929b4a93d..6d9af3bb7d7d` contains **no merge commits**, so no new integration bridge is
needed; `mirror-replay-2491.json` differs from `mirror-replay-2409.json` only in `source_tip`.
All 82 entries are mapped to 29 feature documents in the README (section "Mirror follow-ups 2410–2491");
the requested capacity-evidence entries are **2485–2491**
(`c90395a43bcf` … `6d9af3bb7d7d`, [kanban-capacity-evidence.md](kanban-capacity-evidence.md)).

## Export and normalization

Each mail is `git format-patch -1 <sha> --start-number <n>` (exit 0 required, one mail per staging
directory), then the 2409 policy's literal and regex substitutions are applied to everything after the
`From <sha>` separator, and the author header becomes the public no-reply attribution. No description
line needed mbox escaping. No mail carries a binary patch.

- One new reviewed rename: entry 2452 adds a contributor-attribution file whose source path names a
  private worker host. The mail and the public tree use `contributors/emails/ci-worker@example.com`;
  the policy pins it by content (same source and applied blob `c4be062cad8e`), as for the earlier
  attribution renames.
- Two inherited fingerprints were re-pinned because entries 2458 and 2479–2481 changed their source files:
  `tests/hermes_cli/test_kanban_notify_subscribe_target.py` and `tui_gateway/kanban_remote_notices.py`.
  For both, the added/removed lines of the normalized residual diff equal the previously reviewed 2409
  pair exactly (8 and 2 lines; synthetic session IDs, a fixture chat title and a docstring card/chat
  reference). No new functional divergence was admitted.
- `hermes_cli/kanban_placement.py` is unchanged in this range, so the escape-repair overlay keeps its
  exact pre/postimage (`1437c845d99e` → `36c31384b063`). `repair_hostname_escapes_2491.py` and
  `mirror-policy-2491-escape-repair.json` re-bind the 2409 overlay to this snapshot without other changes.

## Executed checks

- Full unchanged CLI verifier with `mirror-policy-2491.json` and `mirror-replay-2491.json`: exit **0**,
  exact output below (273 s).
- Actual README Bash recipe (verifier, segmented replay, additive hostname repair, repaired-tree check):
  exit **0**, output below.
- `python3 -B -m unittest discover -s core -p 'test*.py'`: **66 tests passed** (mirror/helper tests,
  including the re-bound 2491 overlay tests; not the feature suites).
- `core/check_coverage.py`: exit 0 with the new patches and docs staged.
- Patch-index preflight: 24404 distinct nonzero index IDs across all mails and bridges. 463 were absent
  from a fresh fork clone; 455 were copied as individual hash-verified blobs from a previously verified
  source object store. The remaining eight abbreviated IDs (including the known obsolete `43edb3e3f09`,
  `8274bdeb3db`, `9ff9444f10`) are unavailable there too and were not needed by the complete replay.
- Git version matters: Git 2.43's three-way merge conflicts on patch 0032; the replay used Git 2.51.
- Private denylist: zero matches across all 116 new core files and the README; gitleaks: no leaks.
- Immutable-byte audit: the only modified tracked file is `core/README.md`; every other change is a new file.
- The feature test suites were **not** run by this mirror pass.

### Final verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2491, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "6d9af3bb7d7d3da1053792951d5e9ba1025cf90c"}
```

### Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "36c31384b063be9f57e1992abbea3fa52455dc54", "source_tip": "6d9af3bb7d7d3da1053792951d5e9ba1025cf90c", "whole_tree": "pass"}
```
