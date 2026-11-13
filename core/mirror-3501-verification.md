# Mirror snapshot 3501: inventory and whole-tree proof

## Scope

Append **3492–3501**: 10 non-merge source commits through
`4e3a6ab837c6e9eacce922f2f417c0a394bb6f5d`.
Published patches 0001–3491, the 45 bridges, three ancestry-only entries and the verifier remain
unchanged. The upstream exclusion tip stays `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is
still the merge-base. The range contains no merges, so no bridge is added; the replay plan only
moves its `source_tip`.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No new mail-only privacy edit was needed.

Three new patches touch fingerprinted paths: 3492 (`hermes_cli/kanban_placement.py`), 3493
(`tui_gateway/kanban_remote_notices.py`) and 3495–3497 (`tui_gateway/update_pause.py`). Their hunks
contain none of the previously reviewed normalized lines, so carrying the 3491 line mappings changed
no mail line. The three fingerprints are re-pinned to the new source/applied blob pairs:

| Path | Source blob | Applied blob |
|---|---|---|
| `hermes_cli/kanban_placement.py` | `d2ec8e3c5f` | `8e9c720424` |
| `tui_gateway/kanban_remote_notices.py` | `c169e07ac2` | `c123604857` |
| `tui_gateway/update_pause.py` | `0d2ac62e3d` | `4ad11cdf19` |

For each, the new applied blob was checked to equal the normalized new source blob with exactly
the old fingerprint's reviewed line mapping applied (no other difference). No fingerprint is added
or dropped and renames are unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3501.py` /
`mirror-policy-3501-escape-repair.json`. Its BEFORE is the re-pinned applied placement blob
(`8e9c720424`) and its AFTER is the repaired blob (`b377547fa6`); the snapshot still carries three
escaped placement markers.

## Executed checks

- Full unchanged CLI verifier (Git 2.51) with the frozen policy and replay plan: exit 0, including
  exact inventory and whole-tree equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:317`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **139 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README): zero
  matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3501, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "4e3a6ab837c6e9eacce922f2f417c0a394bb6f5d"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "b377547fa611ec4bc872f7f399d883f14a8ef08a", "source_tip": "4e3a6ab837c6e9eacce922f2f417c0a394bb6f5d", "whole_tree": "pass"}
```
