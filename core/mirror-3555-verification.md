# Mirror snapshot 3555: inventory and whole-tree proof

## Scope

Append **3530–3555**: 26 non-merge source commits through
`da34cee8c4f4a701390698287c9d0ae5bdba63ae`.
Published patches 0001–3529, the 45 bridges, three ancestry-only entries and the verifier remain
unchanged. The upstream exclusion tip stays `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is
still the merge-base. The range contains no merges, so no bridge is added; the replay plan only
moves its `source_tip`.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No new mail-only privacy edit was needed.

Three new patches touch fingerprinted paths: 3533 (`tui_gateway/kanban_remote_notices.py`), 3537
(`hermes_cli/kanban_overlap.py`) and 3544 (`tui_gateway/kanban_unloaded.py`). Their hunks contain
none of the previously reviewed normalized lines, so carrying the 3529 line mappings changed no mail
line. The three fingerprints are re-pinned to the new source/applied blob pairs:

| Path | Source blob | Applied blob |
|---|---|---|
| `hermes_cli/kanban_overlap.py` | `fd94fd51b7` | `f87bae4bd0` |
| `tui_gateway/kanban_remote_notices.py` | `7de96c5900` | `ff0a7dd7ad` |
| `tui_gateway/kanban_unloaded.py` | `8ab6fe323a` | `0f3d56eedb` |

For each, the new applied blob was checked to equal the normalized new source blob with exactly
the old fingerprint's reviewed line mapping applied (no other difference). No fingerprint is added
or dropped and renames are unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3555.py` /
`mirror-policy-3555-escape-repair.json`. `hermes_cli/kanban_placement.py` is untouched by this
range, so its BEFORE stays the applied placement blob (`c0c1c2ef19`) and its AFTER the repaired
blob (`36a08cc249`); the snapshot still carries three escaped placement markers.

## Executed checks

- Full unchanged CLI verifier (Git 2.51) with the frozen policy and replay plan: exit 0, including
  exact inventory and whole-tree equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:371`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **155 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README): zero
  matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3555, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "da34cee8c4f4a701390698287c9d0ae5bdba63ae"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "36a08cc24975150f7cc91181bc012a00b1edc126", "source_tip": "da34cee8c4f4a701390698287c9d0ae5bdba63ae", "whole_tree": "pass"}
```
