# Mirror snapshot 3713: inventory and whole-tree proof

## Scope

Append **3690–3713**: 24 non-merge source commits through
`126d9a35b53e0c3694a17c3161e0fc6fed99f3f2`. Published patches 0001–3689, the 46 earlier
bridges, three ancestry-only entries and the verifier remain unchanged. The upstream exclusion
tip and merge-base stay `55b0b2611f683a12450163b9fe423abbd61a9923`. Replay still starts at
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range `bf08acbc3e..126d9a35b5` contains no merges, so the entries are in plain
`git rev-list --reverse --no-merges` order and need no new bridge
([mirror-replay-3713.json](mirror-replay-3713.json) carries the same 46 hash-pinned bridges).

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No mail-only privacy edit was needed: the new
mails were scanned for private network addresses, account numbers and tailnet names, with no hits.

No new entry touches a fingerprinted path, so all 52 fingerprints and the 8 rename rules carry
over unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3713.py` /
`mirror-policy-3713-escape-repair.json` (BEFORE `173f64cda6`, AFTER `54335f367d`, three markers).

## Executed checks

- Full unchanged CLI verifier (Git 2.51): exit 0, including exact inventory and whole-tree
  equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`3184:466`, bridge before 3651, `3650:63`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **195 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README):
  zero matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "55b0b2611f683a12450163b9fe423abbd61a9923", "bridges": 46, "merge_base": "55b0b2611f683a12450163b9fe423abbd61a9923", "patches": 3713, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "126d9a35b53e0c3694a17c3161e0fc6fed99f3f2"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "54335f367df4b41ea004a945bfe0635fbe8ffdd7", "source_tip": "126d9a35b53e0c3694a17c3161e0fc6fed99f3f2", "whole_tree": "pass"}
```
