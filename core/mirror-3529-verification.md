# Mirror snapshot 3529: inventory and whole-tree proof

## Scope

Append **3502–3529**: 28 non-merge source commits through
`43899644c6f537c4c0d33f3091604d75e3848094`.
Published patches 0001–3501, the 45 bridges, three ancestry-only entries and the verifier remain
unchanged. The upstream exclusion tip stays `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is
still the merge-base. The range contains no merges, so no bridge is added; the replay plan only
moves its `source_tip`.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No new mail-only privacy edit was needed.

Five new patches touch fingerprinted paths: 3502 (`hermes_cli/kanban_watch.py`,
`tests/tools/test_kanban_watch_park.py`), 3506 (`hermes_cli/kanban_overlap.py`), 3516
(`tui_gateway/kanban_remote_notices.py`, `tests/tui_gateway/test_kanban_remote_notices.py`) and 3522
(`hermes_cli/kanban_placement.py`). Their hunks contain none of the previously reviewed normalized
lines, so carrying the 3501 line mappings changed no mail line. The six fingerprints are re-pinned to
the new source/applied blob pairs:

| Path | Source blob | Applied blob |
|---|---|---|
| `hermes_cli/kanban_overlap.py` | `e0a6d3b47c` | `83c10e1934` |
| `hermes_cli/kanban_placement.py` | `1e3dfe010c` | `c0c1c2ef19` |
| `hermes_cli/kanban_watch.py` | `908ad748ab` | `3272686a64` |
| `tests/tools/test_kanban_watch_park.py` | `00bbb5b8c4` | `d9809c70f8` |
| `tests/tui_gateway/test_kanban_remote_notices.py` | `41c4f396fc` | `73f3d7284a` |
| `tui_gateway/kanban_remote_notices.py` | `6905b1dd65` | `a1319f7459` |

For each, the new applied blob was checked to equal the normalized new source blob with exactly
the old fingerprint's reviewed line mapping applied (no other difference). No fingerprint is added
or dropped and renames are unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3529.py` /
`mirror-policy-3529-escape-repair.json`. Its BEFORE is the re-pinned applied placement blob
(`c0c1c2ef19`) and its AFTER is the repaired blob (`36a08cc249`); the snapshot still carries three
escaped placement markers.

## Executed checks

- Full unchanged CLI verifier (Git 2.51) with the frozen policy and replay plan: exit 0, including
  exact inventory and whole-tree equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:345`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **147 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README): zero
  matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3529, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "43899644c6f537c4c0d33f3091604d75e3848094"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "36a08cc24975150f7cc91181bc012a00b1edc126", "source_tip": "43899644c6f537c4c0d33f3091604d75e3848094", "whole_tree": "pass"}
```
