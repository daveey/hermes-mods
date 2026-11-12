# Mirror snapshot 2726: inventory and whole-tree proof

## Scope

Append **2679–2726** (48 non-merge source commits) to the immutable 2678 series.
Frozen source: `53c8685517ca77347d7abe2f646cfa21bdb62895`.
Frozen upstream exclusion: `e3bf4be5df04cf72dab4914b073361c71af1dbe1` (unchanged).
Merge-base: `78abaafcccec20a546c94e8734a70e38c08a6a69` (unchanged).
Published ordinals, old mail bytes, all 44 bridges, the three ancestry-only entries, all historical
policies and `verify_mirror.py` are unchanged. The replay still starts at the historical pinned start
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range `d43896b87835..53c8685517ca` contains **no merge commits**, so no new integration bridge is
needed; `mirror-replay-2726.json` and `mirror-policy-2726.json` differ from their 2678 counterparts only in
`source_tip`. All 48 entries are mapped to feature documents in the README (section "Mirror follow-ups
2679–2726"): 12 new documents plus follow-up sections in seven existing ones.
Requested entries: execution observations (PR #1263) are **2679–2697**, documented in
[execution-observations.md](execution-observations.md).

## Export and normalization

Each mail is `git format-patch -q -1 <sha> --start-number <n>` (exit 0, one mail per staging directory),
then the 2678 policy's literal and regex substitutions are applied after the `From <sha>` separator and the
author header becomes the public no-reply attribution. For every mail the plain normalization equals the
escape-preserving `normalize_regex`. Every mail has exactly one `From ` separator; no binary patches; no mail
was edited by hand. Two mails changed under normalization: **2720** (a product name in the commit message)
and **2722** (an organization/repository name in the commit message and a test file); the whole-tree check
accepts both through the existing policy substitutions.

Fingerprints and overlay:

- No new rename, no new fingerprint and no re-pin: the range touches no fingerprinted path.
- `hermes_cli/kanban_placement.py` is unchanged in this range, so the escape-repair overlay keeps the same
  exact pre/postimage (`1d2b722ea036` → `4effef78af5d`). `repair_hostname_escapes_2726.py`,
  `test_repair_hostname_escapes_2726.py` and `mirror-policy-2726-escape-repair.json` differ from their 2678
  counterparts only in the snapshot number and `source_tip`.

## Executed checks

- Full unchanged CLI verifier with `mirror-policy-2726.json` and `mirror-replay-2726.json`, Git 2.51: exit
  **0** (307 s), exact output below.
- Actual README Bash recipe (verifier, segmented replay, additive hostname repair, repaired-tree check),
  extracted from the README and run verbatim with Git 2.51: exit **0** (465 s), output below.
- `python3 -B -m unittest discover -s core -p 'test*.py'`: **91 tests passed** (mirror/helper tests only,
  not the feature suites).
- `core/check_coverage.py`: exit 0 with the new patches and docs.
- Patch-index preflight: of 25,646 abbreviated index IDs, seven are absent from the fork clone; all belong to
  older mails (0402, 0616, 0631, 1506) and were not needed by the complete replay, as in earlier passes. All
  index IDs of the 48 new mails are present.
- Private denylist: zero matches across all new and changed core files and the README; gitleaks: no leaks.
- Immutable-byte audit: modified tracked files are `core/README.md` and seven extended docs; every other
  change is a new file.
- The feature test suites were **not** run by this mirror pass.

### Final verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2726, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "53c8685517ca77347d7abe2f646cfa21bdb62895"}
```

### Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "4effef78af5dc6758780d7fa6d38b35f446bbcb8", "source_tip": "53c8685517ca77347d7abe2f646cfa21bdb62895", "whole_tree": "pass"}
```
