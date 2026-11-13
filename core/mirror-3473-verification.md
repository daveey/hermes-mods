# Mirror snapshot 3473: inventory and whole-tree proof

## Scope

Append **3424–3473**: 50 non-merge source commits through
`bfe6e0898ce2324faa97a0aa458de9dfb1151dba`. PR #2176 (remote card adapter error, one record) is
**3445**; the sidebar dot "status unknown" label is **3469**.
Published patches 0001–3423, the 45 bridges, three ancestry-only entries and the verifier remain
unchanged. The upstream exclusion tip stays `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is
still the merge-base. The range contains no merges, so no bridge is added; the replay plan only
moves its `source_tip`.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No new mail-only privacy edit was needed.

Earlier fingerprints' reviewed line mappings were carried into the new mails' hunks for the same
paths; no new mail line needed one. Six fingerprints are re-pinned because new patches changed
their source files: `hermes_cli/kanban_placement.py`, `tests/hermes_cli/test_kanban_placement.py`,
`tests/tui_gateway/test_kanban_remote_notices.py`, `tui_gateway/kanban_remote_notices.py`,
`tui_gateway/kanban_unloaded.py`, `tui_gateway/update_pause.py`. Each new applied blob was checked
to equal its new normalized source with exactly the previous fingerprint's line mapping applied
(line by line, no other difference). No fingerprint is added or dropped; renames are unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3473.py` /
`mirror-policy-3473-escape-repair.json` (BEFORE is the re-pinned placement blob
`2579ffc51b`, AFTER `b8c1c7f48b`); the snapshot still carries three escaped placement markers.

## Executed checks

- Full unchanged CLI verifier (Git 2.51) with the frozen policy and replay plan: exit 0, including
  exact inventory and whole-tree equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:289`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **123 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README): zero
  matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3473, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "bfe6e0898ce2324faa97a0aa458de9dfb1151dba"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "b8c1c7f48bcb92f7996e8c8f5762aa081c6adbaf", "source_tip": "bfe6e0898ce2324faa97a0aa458de9dfb1151dba", "whole_tree": "pass"}
```
