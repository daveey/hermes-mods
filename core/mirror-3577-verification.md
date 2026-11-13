# Mirror snapshot 3577: inventory and whole-tree proof

## Scope

Append **3556–3577**: 22 non-merge source commits through
`346b6b7b348c7de73cf2f158714c0657c04ed611`.
Published patches 0001–3555, the 45 bridges, three ancestry-only entries and the verifier remain
unchanged. The upstream exclusion tip stays `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is
still the merge-base. The range contains no merges, so no bridge is added; the replay plan only
moves its `source_tip`.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No new mail-only privacy edit was needed.

Three new patches touch fingerprinted paths: 3556 (`tui_gateway/kanban_remote_notices.py`), 3558
(`hermes_cli/kanban_placement.py`) and 3561 (`tui_gateway/update_pause.py`). Their hunks contain
none of the previously reviewed normalized lines, so carrying the 3555 line mappings changed no mail
line. The three fingerprints are re-pinned to the new source/applied blob pairs:

| Path | Source blob | Applied blob |
|---|---|---|
| `hermes_cli/kanban_placement.py` | `463964d21e` | `0f4083c1cd` |
| `tui_gateway/kanban_remote_notices.py` | `31b34d59f4` | `039ce29e0d` |
| `tui_gateway/update_pause.py` | `e8ab0b8ff7` | `8a9a99ff68` |

For each, the new applied blob was checked to equal the normalized new source blob with exactly
the old fingerprint's reviewed line mapping applied (no other difference). No fingerprint is added
or dropped and renames are unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3577.py` /
`mirror-policy-3577-escape-repair.json`. Patch 3558 changes `hermes_cli/kanban_placement.py`, so
its BEFORE is the re-pinned applied placement blob (`0f4083c1cd`) and its AFTER the repaired blob
(`238f471eec`); the snapshot still carries three escaped placement markers.

## Executed checks

- Full unchanged CLI verifier (Git 2.51) with the frozen policy and replay plan: exit 0, including
  exact inventory and whole-tree equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:393`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **163 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README): zero
  matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3577, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "346b6b7b348c7de73cf2f158714c0657c04ed611"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "238f471eece5467a24358e589a1cac32a8c966bf", "source_tip": "346b6b7b348c7de73cf2f158714c0657c04ed611", "whole_tree": "pass"}
```
