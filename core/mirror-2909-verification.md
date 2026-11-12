# Mirror snapshot 2909: inventory and whole-tree proof

## Scope

Append **2727–2909**: 183 non-merge commits after snapshot 2726 through
`79dfcc03fbd86bb2b0566c6cec520aac044148ec`.
Upstream exclusion remains `e3bf4be5df04cf72dab4914b073361c71af1dbe1`;
merge-base remains `78abaafcccec20a546c94e8734a70e38c08a6a69`.
No source merges occur in the appended range. All published patch bytes, 44 bridges,
three ancestry-only entries, historical policies and the verifier remain unchanged.
PR #1663 is **2908–2909**, documented in [execution-observations.md](execution-observations.md).

## Export and normalization

All 183 entries were exported with successful `git format-patch -1` into separate staging
directories, then normalized with the preceding policy's literal and regex substitutions
and the public no-reply author. One description-only mail separator is required per entry.
Patch 2786 also preserves an existing sanitized comment context in `tui_gateway/update_pause.py`;
its exact line mapping is derived from the preceding snapshot's source/applied fingerprint pair.
No published entry was regenerated. No new fingerprint or rename was introduced.

Eight existing fingerprints were re-pinned after their files changed: `hermes_cli/kanban_origin.py`,
`hermes_cli/kanban_placement.py`, `tests/hermes_cli/test_kanban_origin_adoption.py`,
`tests/hermes_cli/test_kanban_placement.py`, `tests/tui_gateway/test_notice_guarantee.py`,
`tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_unloaded.py`, and
`tui_gateway/update_pause.py`. For each, the new applied blob was independently checked
byte-for-byte against the new source blob under the existing substitutions plus the exact
historical source/applied line mappings. Thus these retain only the reviewed historical
normalizations, not additional code differences.

The mandatory hostname repair is copied to snapshot 2909, with exact preimage
`58a910353b9ccad5237a067d988b2d977bc9d14f` and postimage
`a2599cdac65221b6aca848f8ec8d5abf5a9e687d`. It still repairs precisely two regex word
boundaries; marker compilation and the entire repaired tree are checked.

## Executed checks

- Unchanged CLI verifier with the new policy/replay plan: **exit 0**.
- Actual README Bash recipe, including its verifier, all segmented mail/bridge application,
  additive repair and repaired-tree check: **exit 0**. The last slice is 654 entries from offset 2255.
- Mirror/helper unittest discovery: **99 tests passed**. Feature suites were **not run** by this mirror pass.
- README rows cover all 183 appended ordinals exactly once; every link targets a feature doc.
- Historical blob preflight fetched available normalized index objects read-only; 7 old index
  IDs remain absent but were not required by either successful complete replay.
- Private denylist scan across changed/new core files: zero matches.

### Full verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2909, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "79dfcc03fbd86bb2b0566c6cec520aac044148ec"}
```

### Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "a2599cdac65221b6aca848f8ec8d5abf5a9e687d", "source_tip": "79dfcc03fbd86bb2b0566c6cec520aac044148ec", "whole_tree": "pass"}
```
