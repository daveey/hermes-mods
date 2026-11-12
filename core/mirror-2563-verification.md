# Mirror snapshot 2563: inventory and whole-tree proof

## Scope

Append **2492–2563** (72 non-merge source commits) to the immutable 2491 series.
Frozen source: `d7b4c4cdccc4cb4a82a389c927883059383efb49`.
Frozen upstream exclusion: `e3bf4be5df04cf72dab4914b073361c71af1dbe1` (unchanged).
Merge-base: `78abaafcccec20a546c94e8734a70e38c08a6a69` (unchanged).
Published ordinals, old mail bytes, all 44 bridges, the three ancestry-only entries, all historical
policies and `verify_mirror.py` are unchanged. The replay still starts at the historical pinned start
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range `6d9af3bb7d7d..d7b4c4cdccc4` contains **no merge commits**, so no new integration bridge is
needed; `mirror-replay-2563.json` differs from `mirror-replay-2491.json` only in `source_tip`.
All 72 entries are mapped to 31 feature documents in the README (section "Mirror follow-ups 2492–2563").
Requested entries: the recommendation nudge is **2518–2523** (`5fc0e16993ec` … `476e8c7f86a3`,
[recommendation-nudge.md](recommendation-nudge.md)); one-failure-per-run finalization is **2554–2563**
(`e86edd3cef90` … `d7b4c4cdccc4`, [kanban-failure-once.md](kanban-failure-once.md)).

## Export and normalization

Each mail is `git format-patch -q -1 <sha> --start-number <n>` (exit 0 required, exactly one mail per
staging directory), then the 2491 policy's literal and regex substitutions are applied to everything after
the `From <sha>` separator, and the author header becomes the public no-reply attribution. Two mails changed
under normalization (2521: one test-fixture phrase; 2537: one fixture-provenance sentence). For every mail the
plain normalization equals the escape-preserving `normalize_regex`, so no new hostname-regex escape hazard was
introduced. No description line needed mbox escaping; every mail has exactly one `From ` separator. No mail
carries a binary patch.

- No new rename.
- Two inherited fingerprints were re-pinned because this range changed their source files:
  `hermes_cli/kanban_placement.py` (entries 2498, 2524) and `tui_gateway/kanban_unloaded.py` (entry 2515).
  For both, the added/removed lines of the normalized residual diff equal the previously reviewed 2491 pair
  exactly (8 and 2 lines). No new functional divergence was admitted.
- Because `hermes_cli/kanban_placement.py` changed, the mandatory escape-repair overlay was re-bound to the new
  exact preimage (`e1a3e0a8458f` → `7a86da9d9b42`): the same two `\mini-1\b` → `\bmini-1\b` replacements, and
  the marker table compiles afterwards (the preimage still fails with `bad escape \m`).
  `repair_hostname_escapes_2563.py`, `test_repair_hostname_escapes_2563.py` and
  `mirror-policy-2563-escape-repair.json` differ from their 2491 counterparts only in the snapshot number and
  these two blob IDs.
- `.gitleaksignore` gains two line-scoped `generic-api-key` entries for the synthetic `AIAgent` test-fixture key
  in entries 2539 and 2540 (the same upstream fixture pattern as the 2409 entry).

## Executed checks

- Full unchanged CLI verifier with `mirror-policy-2563.json` and `mirror-replay-2563.json`: exit **0**,
  exact output below (134 s).
- Actual README Bash recipe (verifier, segmented replay, additive hostname repair, repaired-tree check):
  exit **0** (376 s, including its own full verifier run), output below.
- `python3 -B -m unittest discover -s core -p 'test*.py'`: **74 tests passed** (mirror/helper tests,
  including the re-bound 2563 overlay tests; not the feature suites).
- `core/check_coverage.py`: exit 0 with the new patches and docs staged.
- Patch-index preflight: 24822 distinct nonzero index IDs across all mails and bridges. 962 were absent
  from a fresh fork clone plus the upstream `main` fetch; 954 were copied read-only from the hub's source
  object store as individual hash-verified blobs. The remaining eight abbreviated IDs (the same eight as the
  2491 pass, including `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`) are unavailable there too and were not
  needed by the complete replay. All index IDs of the 72 new mails are present.
- Git version matters: Git 2.43's three-way merge conflicts on patch 0032; the replay used Git 2.51.
- Private denylist: zero matches across all new core files and the README; gitleaks: no leaks.
- Immutable-byte audit: the only modified tracked files are `core/README.md` and `.gitleaksignore`
  (two appended lines plus a comment); every other change is a new file.
- The feature test suites were **not** run by this mirror pass.

### Final verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2563, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "d7b4c4cdccc4cb4a82a389c927883059383efb49"}
```

### Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "7a86da9d9b42f6846c22eab4e9c35df7c8bfa798", "source_tip": "d7b4c4cdccc4cb4a82a389c927883059383efb49", "whole_tree": "pass"}
```
