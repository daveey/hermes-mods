# Mirror snapshot 2678: inventory and whole-tree proof

## Scope

Append **2564–2678** (115 non-merge source commits) to the immutable 2563 series.
Frozen source: `d43896b87835ea73ab12bc12dcbcf0fcd088d1b5`.
Frozen upstream exclusion: `e3bf4be5df04cf72dab4914b073361c71af1dbe1` (unchanged).
Merge-base: `78abaafcccec20a546c94e8734a70e38c08a6a69` (unchanged).
Published ordinals, old mail bytes, all 44 bridges, the three ancestry-only entries, all historical
policies and `verify_mirror.py` are unchanged. The replay still starts at the historical pinned start
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range `d7b4c4cdccc4..d43896b87835` contains **no merge commits**, so no new integration bridge is
needed; `mirror-replay-2678.json` differs from `mirror-replay-2563.json` only in `source_tip`.
All 115 entries are mapped to feature documents in the README (section "Mirror follow-ups 2564–2678"):
35 new `*-2678.md` documents plus extensions of three existing ones.
Requested entry: the foreign Desktop local-hash owner is **2677** (`fa489e21c352`), documented as a
follow-up in [kanban-desktop-owner-provenance-2409a.md](kanban-desktop-owner-provenance-2409a.md).

## Export and normalization

Each mail is `git format-patch -q -1 <sha> --start-number <n>` (exit 0, one mail per staging directory),
then the 2563 policy's literal and regex substitutions are applied after the `From <sha>` separator and the
author header becomes the public no-reply attribution. For every mail the plain normalization equals the
escape-preserving `normalize_regex`. Every mail has exactly one `From ` separator; no binary patches.

Four mails were edited by hand after normalization:

- **2627**: one private host name dropped from the commit message (`the M4 56 h case`).
- **2568, 2570, 2650**: diff context lines (including one hunk-header context) rewritten to the spelling the already-published series carries
  at that point (the policy rewrites the source spelling, but the published preimage keeps the earlier
  public spelling), so the mails apply on the published series. Only context lines changed; no added or
  removed line differs from the normalized export.

Fingerprints:

- No new rename and no new functional divergence.
- Six inherited fingerprints were re-pinned because this range changed their source files:
  `hermes_cli/kanban_origin.py`, `hermes_cli/kanban_placement.py`, `tests/hermes_cli/test_kanban_placement.py`,
  `tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_unloaded.py`, `tui_gateway/update_pause.py`.
  For each, the added/removed lines of the normalized residual diff equal the previously reviewed 2563 pair
  exactly (2, 8, 20, 2, 2 and 2 lines).
- Because `hermes_cli/kanban_placement.py` changed, the mandatory escape-repair overlay was re-bound to the new
  exact preimage (`1d2b722ea036` → `4effef78af5d`): the same two `\mini-1\b` → `\bmini-1\b` replacements; the
  preimage still fails with `bad escape \m` and the result compiles.
- The new placement source builds one table marker with `+` (`DESKTOP_ACTION_MARKER`). The shared
  `repair_hostname_escapes.validate_markers` now folds `+` of string literals and earlier module-level names
  from the AST (still no import or execution); an unresolvable name still raises. A new test covers it.
  `repair_hostname_escapes_2678.py`, `test_repair_hostname_escapes_2678.py` and
  `mirror-policy-2678-escape-repair.json` differ from their 2563 counterparts only in the snapshot number and
  the two blob IDs.
- `.gitleaksignore` gains five line-scoped `generic-api-key` entries for synthetic redactor test fixtures
  (documented AWS example key and obvious fake values) in entries 2633 and 2634.

## Executed checks

- Full unchanged CLI verifier with `mirror-policy-2678.json` and `mirror-replay-2678.json`: exit **0**,
  exact output below (416 s).
- Actual README Bash recipe (verifier, segmented replay, additive hostname repair, repaired-tree check),
  extracted from the README and run verbatim with Git 2.51: exit **0** (670 s), output below.
- `python3 -B -m unittest discover -s core -p 'test*.py'`: **83 tests passed** (mirror/helper tests only,
  not the feature suites).
- `core/check_coverage.py`: exit 0 with the new patches and docs.
- Patch-index preflight: 962 index IDs were absent from a fresh fork clone plus the upstream `main` fetch;
  950 were copied read-only from the hub's source object store as hash-verified blobs. The remaining twelve
  abbreviated IDs all belong to older mails or bridges (the eight from earlier passes plus four in bridges)
  and were not needed by the complete replay. All index IDs of the 115 new mails are present.
- Private denylist: zero matches across all new and changed core files and the README; gitleaks: no leaks.
- Immutable-byte audit: modified tracked files are `core/README.md`, `.gitleaksignore`, three extended docs,
  `core/repair_hostname_escapes.py` and `core/test_repair_hostname_escapes.py`; every other change is a new file.
- The feature test suites were **not** run by this mirror pass.

### Final verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2678, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "d43896b87835ea73ab12bc12dcbcf0fcd088d1b5"}
```

### Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "4effef78af5dc6758780d7fa6d38b35f446bbcb8", "source_tip": "d43896b87835ea73ab12bc12dcbcf0fcd088d1b5", "whole_tree": "pass"}
```
